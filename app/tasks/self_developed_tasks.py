from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.time_utils import beijing_now

logger = logging.getLogger(__name__)


@celery_app.task(name="platform.import_self_developed_from_path1")
def import_self_developed_from_path1(
    task_id: str,
    local_base_path: Optional[str] = None,
    repo_name: Optional[str] = None,
    file_types: Optional[str] = None,
) -> Dict[str, Any]:
    """按 scan_result_code_clone.path1 从本地导入自研代码到 code_file。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.code_file import CodeFile
    from app.models.scan_result_code_clone import ScanResultCodeClone

    base = Path(local_base_path or settings.self_developed_base_path)
    types = {
        t.strip().lower()
        for t in (file_types or settings.self_developed_file_types).split(",")
        if t.strip()
    }
    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    stored = 0
    skipped = 0
    try:
        rows: List[ScanResultCodeClone] = (
            session.query(ScanResultCodeClone).filter(ScanResultCodeClone.task_id == task_id).all()
        )
        seen = set()
        for r in rows:
            p1 = (r.path1 or "").replace("\\", "/")
            if not p1 or p1 in seen:
                continue
            seen.add(p1)
            # 按 path1_prefix 截取相对路径
            rel = p1
            prefix = settings.path1_prefix.replace("\\", "/")
            idx = rel.lower().find(prefix.lower())
            if idx >= 0:
                rel = rel[idx + len(prefix) :]
            local = base / rel
            if not local.is_file():
                # 尝试仅文件名
                local = base / Path(p1).name
            if not local.is_file():
                skipped += 1
                continue
            if local.suffix.lower() not in types:
                skipped += 1
                continue
            try:
                content = local.read_text(encoding="utf-8")
            except Exception:
                skipped += 1
                continue
            session.add(
                CodeFile(
                    ingest_id=None,
                    task_id=task_id,
                    file_path=rel or p1,
                    content=content,
                    file_size=len(content.encode("utf-8", errors="ignore")),
                    extension=local.suffix.lower(),
                    created_at=beijing_now(),
                )
            )
            stored += 1
            if stored % 50 == 0:
                session.commit()
        session.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "repo_name": repo_name or settings.self_developed_repo_name,
            "stored": stored,
            "skipped": skipped,
        }
    except Exception as e:
        session.rollback()
        logger.exception("import_self_developed_from_path1 failed: %s", e)
        return {"ok": False, "error": str(e), "task_id": task_id}
    finally:
        session.close()
        eng.dispose()
