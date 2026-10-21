from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.time_utils import beijing_now

logger = logging.getLogger(__name__)


@celery_app.task(name="platform.save_file_ingest_result")
def save_file_ingest_result(
    source_type: str,
    source_label: Optional[str],
    meta: Dict[str, Any],
    s3_upload_status: Optional[str] = None,
    source_file_path: Optional[str] = None,
    task_id: Optional[str] = None,
    status: int = 1,
) -> Dict[str, Any]:
    """异步写入 file_ingest_result（规格：响应中 ingest_id 可为 null）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.file_ingest import FileIngestResult

    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    try:
        row = FileIngestResult(
            source_type=source_type,
            source_label=(source_label[:512] if source_label else None),
            meta=meta,
            tree=None,
            s3_upload_status=s3_upload_status,
            source_file_path=source_file_path,
            task_id=task_id,
            status=status,
            created_at=beijing_now(),
        )
        session.add(row)
        session.commit()
        logger.info("save_file_ingest_result — id=%s task_id=%s", row.id, task_id)
        return {"status": "success", "ingest_id": row.id, "task_id": task_id}
    except Exception as e:
        session.rollback()
        logger.exception("save_file_ingest_result failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        session.close()
        eng.dispose()
