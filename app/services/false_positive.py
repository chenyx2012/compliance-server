"""
误报分类核心逻辑（规格 §12）。

llm：开源大模型 step1 ai-analyze →（视需要）step2 comparison-analyze（旧值 huawei_ai 视为 llm）
selfdeveloped_ai：直连 Qwen /predict
dual：大模型初判与 Qwen 初判并行；两者都判无风险才直接无风险，否则走 step2

模型调用失败时一律判「需要人工确认」并带 ai_error=True，且不写缓存——
宁可多送人工，也不能因模型不可用把相似代码放行。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.ai_cache import ai_cache_service
from app.services.llm_client import chat_completions, http_client, llm_config

logger = logging.getLogger(__name__)

JUDGMENT_NO_RISK = "无风险"
JUDGMENT_NEED_CONFIRM = "需要人工确认"

AI_TYPES = ("llm", "selfdeveloped_ai", "dual")
_AI_TYPE_ALIASES = {"huawei_ai": "llm"}

_ANALYZE_SYSTEM = (
    "你是开源代码合规误报判定助手。根据自研片段与开源片段的相似度匹配，"
    "判断是否为误报。只输出 JSON："
    '{"judgment":"无风险|需要人工确认","reason":"...","further_analysis":true|false}'
)


def snippet_md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def normalize_snippet(text: str) -> str:
    """折叠空白，避免缩进/换行差异导致同一片段算出不同哈希。"""
    return " ".join((text or "").split())


def case_hash(original_snippet: str, open_source_snippet: str = "") -> str:
    """误报案例键：Redis fp:{hash} 与 ai_false_positive_cases.code_snippet_hash 共用。"""
    return snippet_md5(
        normalize_snippet(original_snippet) + "||" + normalize_snippet(open_source_snippet)
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}


def _content_from_chat(resp: Dict[str, Any]) -> str:
    try:
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(resp, ensure_ascii=False)[:2000]


def _normalize_judgment(value: Any) -> str:
    return JUDGMENT_NO_RISK if value == JUDGMENT_NO_RISK else JUDGMENT_NEED_CONFIRM


def _cacheable(result: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in result.items() if k != "cache_hit"}


def ai_analyze(
    *,
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    key = case_hash(original_snippet, open_source_snippet)
    if use_cache:
        cached = ai_cache_service.get(key, "ai_analyze")
        if cached:
            return {**cached, "cache_hit": True}

    user = (
        f"相似度: {similarity}\n"
        f"自研片段:\n```\n{(original_snippet or '')[:2000]}\n```\n"
        f"开源片段:\n```\n{(open_source_snippet or '')[:2000]}\n```\n"
        "请判定。"
    )
    try:
        resp = chat_completions(
            messages=[
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            timeout=60.0,
        )
    except Exception as e:
        logger.warning("ai_analyze failed, route to manual review: %s", e)
        return {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": f"AI 不可用，需人工确认: {e}",
            "further_analysis": False,
            "stage": "ai_analyze",
            "raw": "",
            "cache_hit": False,
            "code_snippet_hash": key,
            "ai_error": True,
            "error": str(e),
        }

    content = _content_from_chat(resp)
    parsed = _extract_json(content)
    judgment = _normalize_judgment(parsed.get("judgment"))
    result = {
        "judgment": judgment,
        "reason": parsed.get("reason") or content[:500],
        "further_analysis": bool(parsed.get("further_analysis", judgment == JUDGMENT_NEED_CONFIRM)),
        "stage": "ai_analyze",
        "raw": content[:2000],
        "cache_hit": False,
        "code_snippet_hash": key,
        "ai_error": False,
    }
    if use_cache:
        ai_cache_service.set(key, _cacheable(result), "ai_analyze")
    return result


def comparison_analyze(
    *,
    original_snippet: str,
    open_source_snippet: str,
    first_reason: str = "",
    similarity: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    key = case_hash(original_snippet, open_source_snippet)
    if use_cache:
        cached = ai_cache_service.get(key, "comparison_analyze")
        if cached:
            return {**cached, "cache_hit": True}

    user = (
        f"初判理由: {first_reason}\n相似度: {similarity}\n"
        f"请做二次对比分析，输出 JSON："
        '{"judgment":"无风险|需要人工确认","reason":"..."}\n'
        f"自研:\n```\n{(original_snippet or '')[:1500]}\n```\n"
        f"开源:\n```\n{(open_source_snippet or '')[:1500]}\n```"
    )
    try:
        resp = chat_completions(
            messages=[
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            timeout=90.0,
        )
    except Exception as e:
        logger.warning("comparison_analyze failed, route to manual review: %s", e)
        return {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": f"二次分析失败，需人工确认: {e}",
            "stage": "comparison_analyze",
            "raw": "",
            "cache_hit": False,
            "code_snippet_hash": key,
            "ai_error": True,
            "error": str(e),
        }

    content = _content_from_chat(resp)
    parsed = _extract_json(content)
    result = {
        "judgment": _normalize_judgment(parsed.get("judgment")),
        "reason": parsed.get("reason") or content[:500],
        "stage": "comparison_analyze",
        "raw": content[:2000],
        "cache_hit": False,
        "code_snippet_hash": key,
        "ai_error": False,
    }
    if use_cache:
        ai_cache_service.set(key, _cacheable(result), "comparison_analyze")
    return result


def qwen_predict(
    original_snippet: str,
    open_source_snippet: str = "",
    *,
    with_reasoning: bool = True,
) -> Dict[str, Any]:
    """自研 Qwen LoRA 服务（/predict、/reasoning），片段各截 500 字。"""
    base = (settings.qwen_fp_base_url or "").rstrip("/")
    if not base:
        return {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": "Qwen 误报服务未配置（QWEN_FP_BASE_URL）",
            "source": "selfdeveloped_ai",
            "ai_error": True,
            "error": "QWEN_FP_BASE_URL not set",
        }
    orig = (original_snippet or "")[:500]
    oss = (open_source_snippet or "")[:500]
    payload = {"text": orig, "code": orig, "original_snippet": orig, "open_source_snippet": oss}
    client = http_client()
    try:
        r = client.post(f"{base}/predict", json=payload, timeout=60.0)
        r.raise_for_status()
        pred = r.json()
    except Exception as e:
        logger.warning("qwen_predict failed: %s", e)
        return {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": f"Qwen 不可用，需人工确认: {e}",
            "source": "selfdeveloped_ai",
            "ai_error": True,
            "error": str(e),
        }

    reason = ""
    if with_reasoning:
        try:
            rr = client.post(f"{base}/reasoning", json=payload, timeout=60.0)
            if rr.is_success:
                body = rr.json()
                reason = str(body.get("reasoning") or body.get("reason") or "")
        except Exception:
            pass
    label = str(pred.get("label") or pred.get("judgment") or pred.get("result") or "")
    if JUDGMENT_NO_RISK in label or label.lower() in {"false", "fp", "0", "no_risk"}:
        judgment = JUDGMENT_NO_RISK
    else:
        judgment = JUDGMENT_NEED_CONFIRM
    return {
        "judgment": judgment,
        "reason": reason or str(pred),
        "source": "selfdeveloped_ai",
        "ai_error": False,
    }


def resolve_ai_type(ai_type: Optional[str]) -> str:
    value = (ai_type or "auto").strip().lower()
    value = _AI_TYPE_ALIASES.get(value, value)
    if value in AI_TYPES:
        return value
    return "dual" if settings.qwen_fp_base_url else "llm"


def _final(ai_type: str, judgment: str, reason: str, steps: List[Dict[str, Any]], ai_error: bool) -> Dict[str, Any]:
    return {
        "ai_type": ai_type,
        "judgment": judgment,
        "reason": reason,
        "steps": steps,
        "ai_error": ai_error,
    }


def _llm_flow(
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float],
    use_cache: bool,
) -> Dict[str, Any]:
    step1 = ai_analyze(
        original_snippet=original_snippet,
        open_source_snippet=open_source_snippet,
        similarity=similarity,
        use_cache=use_cache,
    )
    if step1.get("ai_error") or not step1.get("further_analysis"):
        return _final("llm", step1["judgment"], step1["reason"], [step1], bool(step1.get("ai_error")))
    step2 = comparison_analyze(
        original_snippet=original_snippet,
        open_source_snippet=open_source_snippet,
        first_reason=step1.get("reason") or "",
        similarity=similarity,
        use_cache=use_cache,
    )
    return _final("llm", step2["judgment"], step2["reason"], [step1, step2], bool(step2.get("ai_error")))


def _dual_flow(
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float],
    use_cache: bool,
) -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        llm_future = pool.submit(
            ai_analyze,
            original_snippet=original_snippet,
            open_source_snippet=open_source_snippet,
            similarity=similarity,
            use_cache=use_cache,
        )
        qwen_future = pool.submit(qwen_predict, original_snippet, open_source_snippet)
        llm = llm_future.result()
        qwen = qwen_future.result()
    steps: List[Dict[str, Any]] = [llm, qwen]
    ok = [r for r in steps if not r.get("ai_error")]

    if not ok:
        return _final("dual", JUDGMENT_NEED_CONFIRM, "大模型与 Qwen 均不可用，需人工确认", steps, True)

    llm_wants_more = not llm.get("ai_error") and llm.get("further_analysis")
    if all(r["judgment"] == JUDGMENT_NO_RISK for r in ok) and not llm_wants_more:
        reason = "；".join(r.get("reason") or "" for r in ok if r.get("reason"))
        return _final("dual", JUDGMENT_NO_RISK, reason, steps, False)

    if llm.get("ai_error"):
        # 只有 Qwen 可用且未一致判无风险，无法做二次分析
        return _final("dual", JUDGMENT_NEED_CONFIRM, qwen.get("reason") or "", steps, False)

    first_reason = f"大模型: {llm.get('reason') or ''}；Qwen: {qwen.get('reason') or qwen.get('error') or ''}"
    step2 = comparison_analyze(
        original_snippet=original_snippet,
        open_source_snippet=open_source_snippet,
        first_reason=first_reason,
        similarity=similarity,
        use_cache=use_cache,
    )
    steps.append(step2)
    return _final("dual", step2["judgment"], step2["reason"], steps, bool(step2.get("ai_error")))


def integrated_analysis(
    *,
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float] = None,
    ai_type: str = "auto",
    use_cache: bool = True,
) -> Dict[str, Any]:
    """返回 {ai_type, judgment, reason, steps, ai_error}；ai_error=True 表示结论来自兜底而非模型。"""
    mode = resolve_ai_type(ai_type)
    if mode == "dual":
        return _dual_flow(original_snippet, open_source_snippet, similarity, use_cache)
    if mode == "selfdeveloped_ai":
        q = qwen_predict(original_snippet, open_source_snippet)
        if q.get("ai_error") and llm_config.is_configured:
            fallback = _llm_flow(original_snippet, open_source_snippet, similarity, use_cache)
            fallback["ai_type"] = "selfdeveloped_ai_fallback"
            fallback["steps"] = [q, *fallback["steps"]]
            return fallback
        return _final("selfdeveloped_ai", q["judgment"], q.get("reason") or "", [q], bool(q.get("ai_error")))
    return _llm_flow(original_snippet, open_source_snippet, similarity, use_cache)
