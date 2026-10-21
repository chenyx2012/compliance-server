"""批量误报复核的判定流水线（不依赖数据库，便于测试）。

每条相似结果按以下顺序得出结论，前一步命中就不再往下走：
  1. 片段缺失      → 需要人工确认（不调用模型，原因写明缺哪一侧）
  2. Redis fp:{hash} → 复用最终判定
  3. 误报案例库     → ai_false_positive_cases 命中即判无风险
  4. 调用模型       → 同一 hash 只调用一次，线程池并发
模型成功的结论写回 Redis；判为无风险的新案例交给调用方沉淀到案例库。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Tuple

from app.services.ai_cache import ai_cache_service
from app.services.false_positive import (
    JUDGMENT_NEED_CONFIRM,
    JUDGMENT_NO_RISK,
    case_hash,
    integrated_analysis,
    resolve_ai_type,
)

logger = logging.getLogger(__name__)

RAG_REASON_PREFIX = "[误报案例库] "


@dataclass
class VerifyItem:
    key: Hashable
    original: str
    open_source: str
    similarity: Optional[float] = None
    missing: str = ""
    code_type: Optional[str] = None


@dataclass
class Verdict:
    judgment: str
    reason: str
    source: str  # missing_snippet | redis | rag | ai | ai_error
    case_hash: str = ""


@dataclass
class VerifyOutcome:
    verdicts: Dict[Hashable, Verdict] = field(default_factory=dict)
    new_cases: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


RagLookup = Callable[[List[str]], Dict[str, str]]


def judge_items(
    items: Iterable[VerifyItem],
    *,
    ai_type: Optional[str] = None,
    use_redis: bool = True,
    rag_lookup: Optional[RagLookup] = None,
    concurrency: int = 8,
) -> VerifyOutcome:
    mode = resolve_ai_type(ai_type)
    out = VerifyOutcome()
    stats = {
        "total": 0,
        "missing_snippet": 0,
        "redis_hits": 0,
        "rag_hits": 0,
        "ai_calls": 0,
        "ai_errors": 0,
        "no_risk": 0,
        "need_confirm": 0,
    }

    groups: Dict[str, List[VerifyItem]] = {}
    for item in items:
        stats["total"] += 1
        if item.missing:
            out.verdicts[item.key] = Verdict(JUDGMENT_NEED_CONFIRM, item.missing, "missing_snippet")
            stats["missing_snippet"] += 1
            continue
        groups.setdefault(case_hash(item.original, item.open_source), []).append(item)

    def assign(h: str, verdict: Verdict) -> None:
        for it in groups[h]:
            out.verdicts[it.key] = verdict

    pending = list(groups)

    if use_redis and pending:
        for h, cached in ai_cache_service.get_many(pending).items():
            assign(h, Verdict(cached["judgment"], cached.get("reason") or "", "redis", h))
            stats["redis_hits"] += len(groups[h])
        pending = [h for h in pending if groups[h][0].key not in out.verdicts]

    if rag_lookup and pending:
        try:
            known = rag_lookup(pending)
        except Exception as e:
            logger.warning("rag lookup failed, continue with model: %s", e)
            known = {}
        for h, reason in known.items():
            if h in groups:
                assign(h, Verdict(JUDGMENT_NO_RISK, RAG_REASON_PREFIX + (reason or ""), "rag", h))
                stats["rag_hits"] += len(groups[h])
        pending = [h for h in pending if groups[h][0].key not in out.verdicts]

    def analyze(h: str) -> Tuple[str, Dict[str, Any]]:
        rep = groups[h][0]
        return h, integrated_analysis(
            original_snippet=rep.original,
            open_source_snippet=rep.open_source,
            similarity=rep.similarity,
            ai_type=mode,
            use_cache=use_redis,
        )

    if pending:
        stats["ai_calls"] = len(pending)
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for h, result in pool.map(analyze, pending):
                judgment = result.get("judgment") or JUDGMENT_NEED_CONFIRM
                reason = result.get("reason") or ""
                if result.get("ai_error"):
                    stats["ai_errors"] += 1
                    assign(h, Verdict(JUDGMENT_NEED_CONFIRM, reason, "ai_error", h))
                    continue
                assign(h, Verdict(judgment, reason, "ai", h))
                if use_redis:
                    ai_cache_service.set(
                        h,
                        {"judgment": judgment, "reason": reason, "source": "ai", "ai_type": result.get("ai_type")},
                    )
                if judgment == JUDGMENT_NO_RISK:
                    rep = groups[h][0]
                    out.new_cases.append(
                        {
                            "hash": h,
                            "original": rep.original,
                            "reason": reason,
                            "code_type": rep.code_type,
                            "key": rep.key,
                        }
                    )

    for v in out.verdicts.values():
        stats["no_risk" if v.judgment == JUDGMENT_NO_RISK else "need_confirm"] += 1
    out.stats = stats
    return out
