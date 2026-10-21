from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

_IN_CHUNK = 500


def _chunks(seq: Sequence[Any], size: int = _IN_CHUNK) -> Iterator[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _slice_lines(content: Optional[str], start: Optional[int], end: Optional[int]) -> str:
    if not content:
        return ""
    lines = content.splitlines()
    s = max(0, (start or 1) - 1)
    e = end or len(lines)
    return "\n".join(lines[s : max(s + 1, e)])


def _load_items(task_id: str, force: bool) -> Tuple[List[Any], int, int]:
    """读取待复核结果并批量取出两侧片段；返回 (items, total_rows, skipped)。"""
    from app.core.sync_db import sync_session
    from app.models.code_file import CodeFile
    from app.models.open_source_code_file import OpenSourceCodeFile
    from app.models.scan_result_code_clone import ScanResultCodeClone
    from app.services.ai_verify import VerifyItem

    with sync_session() as session:
        rows = (
            session.query(
                ScanResultCodeClone.id,
                ScanResultCodeClone.path1,
                ScanResultCodeClone.start1,
                ScanResultCodeClone.end1,
                ScanResultCodeClone.path2,
                ScanResultCodeClone.start2,
                ScanResultCodeClone.end2,
                ScanResultCodeClone.similarity,
                ScanResultCodeClone.ai_judgment,
            )
            .filter(ScanResultCodeClone.task_id == task_id)
            .all()
        )
        targets = [r for r in rows if force or not r.ai_judgment]

        self_paths = sorted({r.path1 for r in targets if r.path1})
        oss_paths = sorted({r.path2 for r in targets if r.path2})
        self_files: Dict[str, Optional[str]] = {}
        oss_files: Dict[str, Optional[str]] = {}
        for chunk in _chunks(self_paths):
            for path, content in session.query(CodeFile.file_path, CodeFile.content).filter(
                CodeFile.task_id == task_id, CodeFile.file_path.in_(chunk)
            ):
                self_files.setdefault(path, content)
        for chunk in _chunks(oss_paths):
            for path, content in session.query(
                OpenSourceCodeFile.file_path, OpenSourceCodeFile.content
            ).filter(OpenSourceCodeFile.file_path.in_(chunk)):
                if content:
                    oss_files.setdefault(path, content)

    items: List[VerifyItem] = []
    for r in targets:
        original = _slice_lines(self_files.get(r.path1), r.start1, r.end1)
        open_source = _slice_lines(oss_files.get(r.path2), r.start2, r.end2)
        missing = ""
        if not original.strip():
            missing = f"自研片段缺失：code_file 中未找到 {r.path1} 的内容，无法 AI 复核，需人工确认"
        elif not open_source.strip():
            missing = (
                f"开源片段缺失：open_source_code_file 中未找到 {r.path2} 的内容，无法 AI 复核，需人工确认"
                "（PR 模式不导入开源代码，需预先导入开源知识库）"
            )
        items.append(
            VerifyItem(
                key=r.id,
                original=original,
                open_source=open_source,
                similarity=r.similarity,
                missing=missing,
                code_type=(Path(r.path1).suffix.lstrip(".") or None) if r.path1 else None,
            )
        )
    return items, len(rows), len(rows) - len(targets)


def _rag_lookup(hashes: List[str]) -> Dict[str, str]:
    from app.core.sync_db import sync_session
    from app.models.ai_false_positive_case import AiFalsePositiveCase

    found: Dict[str, str] = {}
    with sync_session() as session:
        for chunk in _chunks(hashes):
            for h, reason in session.query(
                AiFalsePositiveCase.code_snippet_hash, AiFalsePositiveCase.judgment_reason
            ).filter(AiFalsePositiveCase.code_snippet_hash.in_(chunk)):
                found[h] = reason or ""
    return found


def _write_back(task_id: str, verdicts: Dict[Any, Any], new_cases: Iterable[Dict[str, Any]]) -> int:
    from sqlalchemy import insert

    from app.core.sync_db import sync_session
    from app.core.time_utils import beijing_now
    from app.models.ai_false_positive_case import AiFalsePositiveCase
    from app.models.scan_result_code_clone import ScanResultCodeClone

    mappings = [
        {"id": row_id, "ai_judgment": v.judgment, "ai_reasoning": v.reason}
        for row_id, v in verdicts.items()
    ]
    cases = list(new_cases)
    with sync_session() as session:
        for chunk in _chunks(mappings):
            session.bulk_update_mappings(ScanResultCodeClone, list(chunk))
        if cases:
            now = beijing_now()
            # 并发任务可能同时沉淀同一案例，靠唯一键 + INSERT IGNORE 去重
            stmt = insert(AiFalsePositiveCase).prefix_with("IGNORE")
            for chunk in _chunks(cases):
                session.execute(
                    stmt,
                    [
                        {
                            "code_snippet": c["original"],
                            "code_snippet_hash": c["hash"],
                            "code_type": c.get("code_type"),
                            "judgment_reason": c.get("reason"),
                            "source": "batch_analysis",
                            "task_id": task_id,
                            "case_index": c.get("key"),
                            "created_at": now,
                            "updated_at": now,
                        }
                        for c in chunk
                    ],
                )
    return len(mappings)


@celery_app.task(name="platform.batch_ai_verify_task")
def batch_ai_verify_task(
    prev_result: Any = None,
    task_id: str = "",
    use_rag: bool = True,
    use_redis: bool = True,
    force: bool = False,
    ai_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    批量回填 scan_result_code_clone.ai_judgment / ai_reasoning。
    判定顺序与规则见 app.services.ai_verify.judge_items。
    """
    if isinstance(prev_result, str) and not task_id:
        task_id = prev_result
        prev_result = None
    if not task_id and isinstance(prev_result, dict):
        task_id = str(prev_result.get("task_id") or "")

    if not task_id:
        return {"ok": False, "error": "task_id is required"}

    from app.services.ai_verify import judge_items

    try:
        items, total_rows, skipped = _load_items(task_id, force)
        outcome = judge_items(
            items,
            ai_type=ai_type or settings.ai_verify_mode,
            use_redis=use_redis,
            rag_lookup=_rag_lookup if use_rag else None,
            concurrency=settings.ai_verify_concurrency,
        )
        updated = _write_back(task_id, outcome.verdicts, outcome.new_cases)
        logger.info(
            "batch_ai_verify_task — task_id=%s rows=%d updated=%d skipped=%d stats=%s new_cases=%d",
            task_id,
            total_rows,
            updated,
            skipped,
            outcome.stats,
            len(outcome.new_cases),
        )
        return {
            "ok": True,
            "task_id": task_id,
            "updated": updated,
            "skipped": skipped,
            "cache_hits": outcome.stats.get("redis_hits", 0),
            "new_cases": len(outcome.new_cases),
            **{k: v for k, v in outcome.stats.items() if k != "total"},
        }
    except Exception as e:
        logger.exception("batch_ai_verify_task failed: %s", e)
        return {"ok": False, "error": str(e), "task_id": task_id}
