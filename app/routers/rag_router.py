"""误报案例库（ai_false_positive_cases）管理。

案例表收录「已确认是误报」的片段对；批量复核时按 code_snippet_hash 精确命中即判无风险。
code_snippet_hash 与 Redis fp:{hash} 同源（false_positive.case_hash），
导入时若提供 open_source_snippet 会一起参与哈希，否则只按自研片段计算。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import func, insert, select

from app.core.sync_db import sync_session
from app.core.time_utils import beijing_now
from app.models.ai_false_positive_case import AiFalsePositiveCase
from app.services.false_positive import case_hash

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.post("/import-json")
def import_json(body: Dict[str, Any]) -> Dict[str, Any]:
    path = body.get("json_file_path") or body.get("path")
    cases = body.get("cases")
    if path:
        p = Path(path)
        if not p.exists():
            return {"success": False, "message": f"file not found: {path}", "imported": 0}
        data = json.loads(p.read_text(encoding="utf-8"))
        cases = data if isinstance(data, list) else data.get("cases") or []
    cases = cases or []

    rows = []
    seen = set()
    now = beijing_now()
    for i, c in enumerate(cases):
        snippet = c.get("code_snippet") or c.get("original_snippet") or ""
        if not snippet:
            continue
        h = c.get("code_snippet_hash") or case_hash(snippet, c.get("open_source_snippet") or "")
        if h in seen:
            continue
        seen.add(h)
        rows.append(
            {
                "code_snippet": snippet,
                "code_snippet_hash": h,
                "code_type": c.get("code_type"),
                "judgment_reason": c.get("judgment_reason") or c.get("reason"),
                "source": c.get("source") or "import-json",
                "task_id": c.get("task_id"),
                "case_index": c.get("case_index", i),
                "created_at": now,
                "updated_at": now,
            }
        )
    if not rows:
        return {"success": True, "message": "rag import done", "imported": 0}

    try:
        with sync_session() as session:
            before = session.execute(select(func.count(AiFalsePositiveCase.id))).scalar_one()
            stmt = insert(AiFalsePositiveCase).prefix_with("IGNORE")
            for i in range(0, len(rows), 500):
                session.execute(stmt, rows[i : i + 500])
            session.flush()
            after = session.execute(select(func.count(AiFalsePositiveCase.id))).scalar_one()
        return {"success": True, "message": "rag import done", "imported": after - before}
    except Exception as e:
        logger.exception("rag import failed: %s", e)
        return {"success": False, "message": str(e), "imported": 0}


@router.get("/stats")
def stats() -> Dict[str, Any]:
    try:
        with sync_session() as session:
            total = session.execute(select(func.count(AiFalsePositiveCase.id))).scalar_one()
            by_source = dict(
                session.execute(
                    select(AiFalsePositiveCase.source, func.count(AiFalsePositiveCase.id)).group_by(
                        AiFalsePositiveCase.source
                    )
                ).all()
            )
        return {"success": True, "total": total, "by_source": by_source}
    except Exception as e:
        return {"success": False, "total": 0, "error": str(e)}


@router.delete("/clear")
def clear() -> Dict[str, Any]:
    try:
        with sync_session() as session:
            n = session.query(AiFalsePositiveCase).delete()
        return {"success": True, "deleted": n}
    except Exception as e:
        return {"success": False, "deleted": 0, "error": str(e)}
