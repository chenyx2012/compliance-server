from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.time_utils import beijing_now
from app.models.ai_false_positive_case import AiFalsePositiveCase
from app.services.false_positive import snippet_md5

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.post("/import-json")
async def import_json(body: Dict[str, Any]) -> Dict[str, Any]:
    path = body.get("json_file_path") or body.get("path")
    cases = body.get("cases")
    if path:
        p = Path(path)
        if not p.exists():
            return {"success": False, "message": f"file not found: {path}", "imported": 0}
        data = json.loads(p.read_text(encoding="utf-8"))
        cases = data if isinstance(data, list) else data.get("cases") or []
    cases = cases or []

    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    imported = 0
    try:
        for i, c in enumerate(cases):
            snippet = c.get("code_snippet") or c.get("original_snippet") or ""
            if not snippet:
                continue
            h = c.get("code_snippet_hash") or snippet_md5(snippet)
            exists = (
                session.query(AiFalsePositiveCase.id)
                .filter(AiFalsePositiveCase.code_snippet_hash == h)
                .one_or_none()
            )
            if exists:
                continue
            session.add(
                AiFalsePositiveCase(
                    code_snippet=snippet,
                    code_snippet_hash=h,
                    code_type=c.get("code_type"),
                    judgment_reason=c.get("judgment_reason") or c.get("reason"),
                    source=c.get("source") or "import-json",
                    task_id=c.get("task_id"),
                    case_index=c.get("case_index", i),
                    created_at=beijing_now(),
                    updated_at=beijing_now(),
                )
            )
            imported += 1
        session.commit()
        return {"success": True, "message": "rag import done", "imported": imported}
    except Exception as e:
        session.rollback()
        logger.exception("rag import failed: %s", e)
        return {"success": False, "message": str(e), "imported": 0}
    finally:
        session.close()
        eng.dispose()


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    try:
        total = session.query(AiFalsePositiveCase).count()
        return {"success": True, "total": total}
    except Exception as e:
        return {"success": False, "total": 0, "error": str(e)}
    finally:
        session.close()
        eng.dispose()


@router.delete("/clear")
async def clear() -> Dict[str, Any]:
    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=eng)
    session = Session()
    try:
        n = session.query(AiFalsePositiveCase).delete()
        session.commit()
        return {"success": True, "deleted": n}
    except Exception as e:
        session.rollback()
        return {"success": False, "deleted": 0, "error": str(e)}
    finally:
        session.close()
        eng.dispose()
