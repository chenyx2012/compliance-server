"""代码克隆结果查询（/code-clone/v1）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.open_source_code_file import OpenSourceCodeFile
from app.models.scan_result_code_clone import ScanResultCodeClone

router = APIRouter(prefix="/code-clone/v1", tags=["code-clone"])


async def _remote_url_for(db: AsyncSession, path2: Optional[str]) -> Optional[str]:
    if not path2:
        return None
    row = (
        await db.execute(
            select(OpenSourceCodeFile.remote_url).where(OpenSourceCodeFile.file_path == path2)
        )
    ).scalar_one_or_none()
    return row


@router.get("/results")
async def list_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=10000),
    task_id: Optional[str] = Query(None),
    similarity_min: Optional[float] = Query(None, ge=0, le=1),
    repo_name: Optional[str] = Query(None, description="逗号多选，按 path2 LIKE {repo}/%"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    q = select(ScanResultCodeClone)
    cq = select(func.count()).select_from(ScanResultCodeClone)
    if task_id:
        q = q.where(ScanResultCodeClone.task_id == task_id)
        cq = cq.where(ScanResultCodeClone.task_id == task_id)
    if similarity_min is not None:
        q = q.where(ScanResultCodeClone.similarity >= similarity_min)
        cq = cq.where(ScanResultCodeClone.similarity >= similarity_min)
    if repo_name:
        repos = [r.strip() for r in repo_name.split(",") if r.strip()]
        if repos:
            conds = [ScanResultCodeClone.path2.like(f"{r}/%") for r in repos]
            q = q.where(or_(*conds))
            cq = cq.where(or_(*conds))

    total = (await db.execute(cq)).scalar_one()
    rows = (
        await db.execute(
            q.order_by(ScanResultCodeClone.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    records: List[Dict[str, Any]] = []
    for r in rows:
        records.append(
            {
                "id": r.id,
                "task_id": r.task_id,
                "path1": r.path1,
                "start1": r.start1,
                "end1": r.end1,
                "path2": r.path2,
                "start2": r.start2,
                "end2": r.end2,
                "similarity": r.similarity,
                "ai_judgment": r.ai_judgment,
                "ai_reasoning": r.ai_reasoning,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "remote_url": await _remote_url_for(db, r.path2),
            }
        )
    total_pages = max(1, math.ceil(total / page_size)) if page_size else 1
    return {
        "ok": True,
        "data": {
            "records": records,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        },
    }


@router.get("/results/{result_id}")
async def get_result(result_id: int, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    r = (
        await db.execute(select(ScanResultCodeClone).where(ScanResultCodeClone.id == result_id))
    ).scalar_one_or_none()
    if r is None:
        return {"ok": False, "error": "结果不存在"}
    return {
        "ok": True,
        "data": {
            "id": r.id,
            "task_id": r.task_id,
            "path1": r.path1,
            "start1": r.start1,
            "end1": r.end1,
            "path2": r.path2,
            "start2": r.start2,
            "end2": r.end2,
            "similarity": r.similarity,
            "ai_judgment": r.ai_judgment,
            "ai_reasoning": r.ai_reasoning,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "remote_url": await _remote_url_for(db, r.path2),
        },
    }
