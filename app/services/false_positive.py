"""
误报分类核心逻辑（规格 §12）。

huawei_ai：step1 ai-analyze →（视需要）step2 comparison-analyze
selfdeveloped_ai：直连 Qwen /predict（可替换为公开模型）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from app.services.ai_cache import ai_cache_service
from app.services.huawei_ai_config import chat_completions, huawei_ai_config

logger = logging.getLogger(__name__)

JUDGMENT_NO_RISK = "无风险"
JUDGMENT_NEED_CONFIRM = "需要人工确认"

_ANALYZE_SYSTEM = (
    "你是开源代码合规误报判定助手。根据自研片段与开源片段的相似度匹配，"
    "判断是否为误报。只输出 JSON："
    '{"judgment":"无风险|需要人工确认","reason":"...","further_analysis":true|false}'
)


def snippet_md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


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


def ai_analyze(
    *,
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    md5 = snippet_md5(original_snippet + "||" + open_source_snippet)
    if use_cache:
        cached = ai_cache_service.get(md5)
        if cached and cached.get("stage") == "ai_analyze":
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
        content = _content_from_chat(resp)
        parsed = _extract_json(content)
        judgment = parsed.get("judgment") or JUDGMENT_NEED_CONFIRM
        if judgment not in (JUDGMENT_NO_RISK, JUDGMENT_NEED_CONFIRM):
            judgment = JUDGMENT_NEED_CONFIRM
        result = {
            "judgment": judgment,
            "reason": parsed.get("reason") or content[:500],
            "further_analysis": bool(parsed.get("further_analysis", judgment == JUDGMENT_NEED_CONFIRM)),
            "stage": "ai_analyze",
            "raw": content[:2000],
            "cache_hit": False,
            "code_snippet_hash": md5,
        }
    except Exception as e:
        logger.warning("ai_analyze fallback: %s", e)
        # 无 AI 时按相似度启发式
        sim = float(similarity or 0)
        judgment = JUDGMENT_NEED_CONFIRM if sim >= 0.85 else JUDGMENT_NO_RISK
        result = {
            "judgment": judgment,
            "reason": f"AI 不可用，启发式判定（similarity={sim}）: {e}",
            "further_analysis": judgment == JUDGMENT_NEED_CONFIRM,
            "stage": "ai_analyze",
            "raw": "",
            "cache_hit": False,
            "code_snippet_hash": md5,
            "error": str(e),
        }

    if use_cache:
        ai_cache_service.set(md5, {k: v for k, v in result.items() if k != "cache_hit"})
    return result


def comparison_analyze(
    *,
    original_snippet: str,
    open_source_snippet: str,
    first_reason: str = "",
    similarity: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    md5 = snippet_md5("cmp:" + original_snippet + "||" + open_source_snippet)
    if use_cache:
        cached = ai_cache_service.get(md5)
        if cached and cached.get("stage") == "comparison_analyze":
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
        content = _content_from_chat(resp)
        parsed = _extract_json(content)
        judgment = parsed.get("judgment") or JUDGMENT_NEED_CONFIRM
        if judgment not in (JUDGMENT_NO_RISK, JUDGMENT_NEED_CONFIRM):
            judgment = JUDGMENT_NEED_CONFIRM
        result = {
            "judgment": judgment,
            "reason": parsed.get("reason") or content[:500],
            "stage": "comparison_analyze",
            "raw": content[:2000],
            "cache_hit": False,
            "code_snippet_hash": md5,
        }
    except Exception as e:
        result = {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": f"二次分析失败: {e}",
            "stage": "comparison_analyze",
            "raw": "",
            "cache_hit": False,
            "code_snippet_hash": md5,
            "error": str(e),
        }
    if use_cache:
        ai_cache_service.set(md5, {k: v for k, v in result.items() if k != "cache_hit"})
    return result


def qwen_predict(snippet: str, *, with_reasoning: bool = True) -> Dict[str, Any]:
    """自研 Qwen LoRA 服务；可替换为公开模型。"""
    base = (os.getenv("QWEN_FP_BASE_URL") or "http://127.0.0.1:8100").rstrip("/")
    text = (snippet or "")[:500]
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{base}/predict", json={"text": text, "code": text})
            r.raise_for_status()
            pred = r.json()
        reason = ""
        if with_reasoning:
            try:
                with httpx.Client(timeout=60.0) as client:
                    rr = client.post(f"{base}/reasoning", json={"text": text, "code": text})
                    if rr.is_success:
                        reason = str(rr.json().get("reasoning") or rr.json().get("reason") or "")
            except Exception:
                pass
        label = str(pred.get("label") or pred.get("judgment") or pred.get("result") or "")
        if "无风险" in label or label.lower() in {"false", "fp", "0", "no_risk"}:
            judgment = JUDGMENT_NO_RISK
        else:
            judgment = JUDGMENT_NEED_CONFIRM
        return {"judgment": judgment, "reason": reason or str(pred), "source": "selfdeveloped_ai"}
    except Exception as e:
        # 回退到公开大模型
        if huawei_ai_config.use_openai_compat or huawei_ai_config.get_dynamic_token():
            r = ai_analyze(original_snippet=text, open_source_snippet="", similarity=None)
            return {
                "judgment": r["judgment"],
                "reason": r["reason"],
                "source": "selfdeveloped_ai_fallback",
                "error": str(e),
            }
        return {
            "judgment": JUDGMENT_NEED_CONFIRM,
            "reason": f"Qwen 不可用: {e}",
            "source": "selfdeveloped_ai",
            "error": str(e),
        }


def integrated_analysis(
    *,
    original_snippet: str,
    open_source_snippet: str,
    similarity: Optional[float] = None,
    ai_type: str = "huawei_ai",
) -> Dict[str, Any]:
    if ai_type == "selfdeveloped_ai":
        q = qwen_predict(original_snippet)
        return {
            "ai_type": ai_type,
            "judgment": q["judgment"],
            "reason": q.get("reason"),
            "steps": [q],
        }

    step1 = ai_analyze(
        original_snippet=original_snippet,
        open_source_snippet=open_source_snippet,
        similarity=similarity,
    )
    steps: List[Dict[str, Any]] = [step1]
    final = step1
    if step1.get("further_analysis"):
        step2 = comparison_analyze(
            original_snippet=original_snippet,
            open_source_snippet=open_source_snippet,
            first_reason=step1.get("reason") or "",
            similarity=similarity,
        )
        steps.append(step2)
        final = step2
    return {
        "ai_type": "huawei_ai",
        "judgment": final.get("judgment"),
        "reason": final.get("reason"),
        "steps": steps,
    }
