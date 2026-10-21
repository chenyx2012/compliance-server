from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(name="platform.batch_ai_verify_task")
def batch_ai_verify_task(
    prev_result: Any = None,
    task_id: str = "",
    use_rag: bool = True,
    use_redis: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    批量回填 scan_result_code_clone.ai_judgment / ai_reasoning。
    命中 Redis 缓存 fp:{md5} 则跳过；否则走 integrated_analysis。
    """
    if isinstance(prev_result, str) and not task_id:
        task_id = prev_result
        prev_result = None
    if not task_id and isinstance(prev_result, dict):
        task_id = str(prev_result.get("task_id") or "")

    if not task_id:
        return {"ok": False, "error": "task_id is required"}

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.code_file import CodeFile
    from app.models.open_source_code_file import OpenSourceCodeFile
    from app.models.scan_result_code_clone import ScanResultCodeClone
    from app.services.ai_cache import ai_cache_service
    from app.services.false_positive import integrated_analysis, snippet_md5

    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    updated = 0
    skipped = 0
    cache_hits = 0
    try:
        rows = session.query(ScanResultCodeClone).filter(ScanResultCodeClone.task_id == task_id).all()
        for row in rows:
            if row.ai_judgment and not force:
                skipped += 1
                continue

            # 取片段内容（尽力）
            orig = ""
            oss = ""
            if row.path1:
                cf = (
                    session.query(CodeFile)
                    .filter(CodeFile.task_id == task_id, CodeFile.file_path == row.path1)
                    .first()
                )
                if cf and cf.content:
                    lines = cf.content.splitlines()
                    s, e = (row.start1 or 1) - 1, (row.end1 or len(lines))
                    orig = "\n".join(lines[max(0, s) : max(s + 1, e)])
            if row.path2:
                of = (
                    session.query(OpenSourceCodeFile)
                    .filter(OpenSourceCodeFile.file_path == row.path2)
                    .first()
                )
                if of and of.content:
                    lines = of.content.splitlines()
                    s, e = (row.start2 or 1) - 1, (row.end2 or len(lines))
                    oss = "\n".join(lines[max(0, s) : max(s + 1, e)])

            md5 = snippet_md5((orig or row.path1 or "") + "||" + (oss or row.path2 or ""))
            if use_redis:
                cached = ai_cache_service.get(md5)
                if cached and cached.get("judgment"):
                    row.ai_judgment = cached["judgment"]
                    row.ai_reasoning = cached.get("reason") or ""
                    cache_hits += 1
                    updated += 1
                    continue

            result = integrated_analysis(
                original_snippet=orig or (row.path1 or ""),
                open_source_snippet=oss or (row.path2 or ""),
                similarity=row.similarity,
                ai_type="huawei_ai",
            )
            row.ai_judgment = result.get("judgment") or "需要人工确认"
            row.ai_reasoning = result.get("reason") or ""
            if use_redis:
                ai_cache_service.set(
                    md5,
                    {
                        "judgment": row.ai_judgment,
                        "reason": row.ai_reasoning,
                        "stage": "batch_ai_verify",
                        "source": "batch_analysis",
                    },
                )
            updated += 1
            if updated % 20 == 0:
                session.commit()

        session.commit()
        logger.info(
            "batch_ai_verify_task — task_id=%s updated=%d skipped=%d cache_hits=%d",
            task_id,
            updated,
            skipped,
            cache_hits,
        )
        return {
            "ok": True,
            "task_id": task_id,
            "updated": updated,
            "skipped": skipped,
            "cache_hits": cache_hits,
        }
    except Exception as e:
        session.rollback()
        logger.exception("batch_ai_verify_task failed: %s", e)
        return {"ok": False, "error": str(e), "task_id": task_id}
    finally:
        session.close()
        eng.dispose()
