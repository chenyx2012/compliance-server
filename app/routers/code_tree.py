"""代码树 / 文件内容查询（/platform/code-tree/v1）。"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, Form, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.code_file import CodeFile
from app.models.file_ingest import FileIngestResult
from app.models.open_source_code_file import OpenSourceCodeFile
from app.models.scan_result_code_clone import ScanResultCodeClone
from app.services.file_ingest import save_code_tree
from app.tasks.csv_parse_tasks import parse_csv_and_save

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/platform/code-tree/v1", tags=["code-tree"])


def _norm_path(p: str) -> str:
    return (p or "").replace("\\", "/").strip()


@router.get("/files")
async def get_files_with_highlights(
    task_id: str = Query(...),
    file_path: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    file_path = _norm_path(file_path)
    clones = (
        await db.execute(
            select(ScanResultCodeClone).where(
                ScanResultCodeClone.task_id == task_id,
                ScanResultCodeClone.path1 == file_path,
            )
        )
    ).scalars().all()
    if not clones:
        return {"ok": False, "error": "文件不存在或未找到匹配的克隆记录"}

    cf = (
        await db.execute(
            select(CodeFile).where(CodeFile.task_id == task_id, CodeFile.file_path == file_path)
        )
    ).scalar_one_or_none()
    if cf is None:
        return {"ok": False, "error": "用户文件不存在"}

    path2_set: Set[str] = {c.path2 for c in clones if c.path2}
    os_map: Dict[str, OpenSourceCodeFile] = {}
    if path2_set:
        rows = (
            await db.execute(
                select(OpenSourceCodeFile).where(OpenSourceCodeFile.file_path.in_(list(path2_set)))
            )
        ).scalars().all()
        os_map = {r.file_path: r for r in rows}

    path2_dict: Dict[str, Any] = {}
    highlight_lines: List[Dict[str, int]] = []
    for c in clones:
        if c.start1 is not None and c.end1 is not None:
            hl = {"start": c.start1, "end": c.end1}
            if hl not in highlight_lines:
                highlight_lines.append(hl)
        p2 = c.path2 or ""
        if not p2:
            continue
        entry = path2_dict.setdefault(
            p2,
            {
                "content": (os_map[p2].content if p2 in os_map else "") or "",
                "highlight_lines2": [],
                "highlight_lines1": [],
            },
        )
        if c.start2 is not None and c.end2 is not None:
            h2 = {"start": c.start2, "end": c.end2}
            if h2 not in entry["highlight_lines2"]:
                entry["highlight_lines2"].append(h2)
        if c.start1 is not None and c.end1 is not None:
            h1 = {"start": c.start1, "end": c.end1}
            if h1 not in entry["highlight_lines1"]:
                entry["highlight_lines1"].append(h1)

    path2_list = list(path2_dict.keys())
    return {
        "ok": True,
        "data": {
            "id": cf.id,
            "ingest_id": cf.ingest_id,
            "task_id": cf.task_id,
            "file_path": cf.file_path,
            "content": cf.content,
            "file_size": cf.file_size,
            "extension": cf.extension,
            "created_at": cf.created_at.isoformat() if cf.created_at else None,
            "highlight_lines": highlight_lines,
            "path2": path2_list,
            "highlighted_lines2": {
                k: v["highlight_lines2"] for k, v in path2_dict.items()
            },
            "path2_content": {k: v["content"] for k, v in path2_dict.items()},
            "path2_dict": path2_dict,
        },
    }


@router.get("/codefile-content")
async def codefile_content(
    task_id: str = Query(...),
    file_path: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    file_path = _norm_path(file_path)
    cf = (
        await db.execute(
            select(CodeFile).where(CodeFile.task_id == task_id, CodeFile.file_path == file_path)
        )
    ).scalar_one_or_none()
    if cf is None:
        return {
            "ok": False,
            "error": "未找到匹配的文件",
            "searched_path": file_path,
            "task_id": task_id,
        }
    return {
        "ok": True,
        "data": {
            "id": cf.id,
            "task_id": cf.task_id,
            "file_path": cf.file_path,
            "content": cf.content,
            "file_size": cf.file_size,
            "extension": cf.extension,
            "ingest_id": cf.ingest_id,
            "created_at": cf.created_at.isoformat() if cf.created_at else None,
        },
    }


@router.get("/opensource-content")
async def opensource_content(
    file_path: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    file_path = _norm_path(file_path)
    row = (
        await db.execute(
            select(OpenSourceCodeFile).where(OpenSourceCodeFile.file_path == file_path)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"ok": False, "error": "未找到匹配的开源文件", "searched_path": file_path}
    return {
        "ok": True,
        "data": {
            "id": row.id,
            "repo_name": row.repo_name,
            "file_path": row.file_path,
            "content": row.content,
            "file_size": row.file_size,
            "extension": row.extension,
            "s3_path": row.s3_path,
            "remote_url": row.remote_url,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        },
    }


@router.get("/clone-paths")
async def clone_paths(
    task_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    rows = (
        await db.execute(
            select(ScanResultCodeClone.path1).where(ScanResultCodeClone.task_id == task_id)
        )
    ).scalars().all()
    paths: List[str] = []
    seen = set()
    for p in rows:
        if not p:
            continue
        raw = p.replace("\\", "/")
        lower = raw.lower()
        rel = raw
        for marker in ("/work/", "/ccfinderx_check_code/"):
            idx = lower.find(marker)
            if idx >= 0:
                rel = raw[idx + len(marker) :]
                break
        else:
            parts = raw.split("/")
            rel = "/".join(parts[-3:]) if len(parts) >= 3 else raw
        if rel not in seen:
            seen.add(rel)
            paths.append(rel)
    return {"ok": True, "data": {"task_id": task_id, "paths": paths, "count": len(paths)}}


@router.post("/ingest/local")
async def ingest_local(
    directory_path: str = Query(...),
    ingest_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    root = Path(directory_path)
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": f"directory not found: {directory_path}"}

    effective_ingest_id = ingest_id
    task_id = None
    if effective_ingest_id is None:
        task_id = "local-" + uuid.uuid4().hex
        row = FileIngestResult(
            source_type="local",
            source_label=directory_path[:512],
            meta={"source": "local", "directory_path": directory_path},
            s3_upload_status="Skipped",
            source_file_path=directory_path,
            task_id=task_id,
            status=1,
        )
        db.add(row)
        await db.flush()
        effective_ingest_id = row.id

    stored = save_code_tree(root, effective_ingest_id, task_id, keep_root_prefix=True)
    return {
        "ok": True,
        "data": {
            "ingest_id": effective_ingest_id,
            "directory_path": directory_path,
            "files_stored": stored,
            "files_skipped": 0,
        },
    }


@router.post("/ingest/csv")
async def ingest_csv(
    csv_path: str = Form(...),
    task_id: str = Form(...),
) -> Dict[str, Any]:
    p = Path(csv_path)
    if not p.exists() or p.suffix.lower() != ".csv":
        return {"ok": False, "error": f"csv_path invalid: {csv_path}"}
    ar = parse_csv_and_save.delay(csv_path, task_id)
    return {
        "ok": True,
        "data": {
            "task_id": task_id,
            "csv_path": csv_path,
            "celery_task_id": ar.id,
            "message": "csv parse queued",
        },
    }
