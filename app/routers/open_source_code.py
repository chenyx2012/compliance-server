"""开源代码导入 / 知识库（挂在 /platform/code-tree/v1）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.open_source_code_file import OpenSourceCodeFile
from app.tasks import open_source_tasks, self_developed_tasks

router = APIRouter(prefix="/platform/code-tree/v1", tags=["open-source-code"])


class S3DownloadBody(BaseModel):
    s3_path: str
    repo_name: str


class ImportDirBody(BaseModel):
    source_path: str
    repo_name: str


class ImportPath1Body(BaseModel):
    task_id: str
    local_base_path: Optional[str] = None
    repo_name: Optional[str] = None
    file_types: Optional[str] = None


class ImportPath2Body(BaseModel):
    task_id: str
    local_base_path: Optional[str] = None
    repo_name: Optional[str] = None


class UpdateRemoteUrlBody(BaseModel):
    repo_name: str
    base_url: str


@router.post("/open-source/download")
async def open_source_download(body: S3DownloadBody) -> Dict[str, Any]:
    if not body.s3_path or not body.repo_name:
        return {"ok": False, "error": "s3_path 和 repo_name 是必填项"}
    ar = open_source_tasks.download_and_save_open_source_code.delay(body.s3_path, body.repo_name)
    return {
        "ok": True,
        "message": "open source download queued",
        "task_id": ar.id,
        "repo_name": body.repo_name,
        "s3_path": body.s3_path,
    }


@router.post("/open-source/import-directory")
async def import_directory(body: ImportDirBody) -> Dict[str, Any]:
    ar = open_source_tasks.save_open_source_code_from_local.delay(body.source_path, body.repo_name)
    return {
        "ok": True,
        "message": "import directory queued",
        "task_id": ar.id,
        "repo_name": body.repo_name,
        "local_path": body.source_path,
    }


@router.post("/self-developed/import-from-path1")
async def import_from_path1(body: ImportPath1Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    ar = self_developed_tasks.import_self_developed_from_path1.delay(
        body.task_id,
        body.local_base_path,
        body.repo_name,
        body.file_types,
    )
    return {
        "ok": True,
        "message": "self-developed import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": body.local_base_path or settings.self_developed_base_path,
        "repo_name": body.repo_name or settings.self_developed_repo_name,
    }


def _queue_path2(body: ImportPath2Body, default_repo: str, default_path: str, task_fn) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    repo = body.repo_name or default_repo
    base = body.local_base_path or default_path
    ar = task_fn.delay(body.task_id, base, repo) if task_fn is open_source_tasks.import_open_source_from_task_path2 else task_fn.delay(body.task_id, repo)
    return {
        "ok": True,
        "message": "open source path2 import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": base,
        "repo_name": repo,
    }


@router.post("/open-source/import-from-task-path2")
async def import_from_task_path2(body: ImportPath2Body) -> Dict[str, Any]:
    return _queue_path2(
        body,
        "cpp",
        settings.open_source_cpp_path,
        open_source_tasks.import_open_source_from_task_path2,
    )


@router.post("/open-source/import-from-task-path2-nvidia")
async def import_path2_nvidia(body: ImportPath2Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    repo = body.repo_name or "nvidia"
    ar = open_source_tasks.import_open_source_from_task_path2_nvidia.delay(body.task_id, repo)
    return {
        "ok": True,
        "message": "nvidia import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": settings.open_source_nvidia_base_path,
        "repo_name": repo,
    }


@router.post("/open-source/import-from-task-path2-cpp")
async def import_path2_cpp(body: ImportPath2Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    repo = body.repo_name or "cpp"
    ar = open_source_tasks.import_open_source_from_task_path2_cpp.delay(body.task_id, repo)
    return {
        "ok": True,
        "message": "cpp import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": settings.open_source_cpp_path,
        "repo_name": repo,
    }


@router.post("/open-source/import-from-task-path2-java")
async def import_path2_java(body: ImportPath2Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    repo = body.repo_name or "java"
    ar = open_source_tasks.import_open_source_from_task_path2_java.delay(body.task_id, repo)
    return {
        "ok": True,
        "message": "java import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": settings.open_source_java_path,
        "repo_name": repo,
    }


@router.post("/open-source/import-from-task-path2-python")
async def import_path2_python(body: ImportPath2Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    repo = body.repo_name or "python"
    ar = open_source_tasks.import_open_source_from_task_path2_python.delay(body.task_id, repo)
    return {
        "ok": True,
        "message": "python import queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": settings.open_source_python_path,
        "repo_name": repo,
    }


@router.post("/open-source/import-from-scan-result")
async def import_from_scan_result(body: ImportPath2Body) -> Dict[str, Any]:
    if not body.task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    ar = open_source_tasks.import_open_source_from_scan_result.delay(body.task_id, body.repo_name)
    return {
        "ok": True,
        "message": "import from scan result queued",
        "task_id": ar.id,
        "scan_task_id": body.task_id,
        "local_path": settings.open_source_cpp_path,
        "repo_name": body.repo_name or "cpp",
    }


@router.get("/knowledge-bases")
async def knowledge_bases(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    rows = (
        await db.execute(
            select(OpenSourceCodeFile.repo_name, func.count(OpenSourceCodeFile.id)).group_by(
                OpenSourceCodeFile.repo_name
            )
        )
    ).all()
    data = [{"repo_name": r[0], "file_count": int(r[1])} for r in rows]
    return {"ok": True, "data": data}


@router.post("/knowledge-bases/update-remote-url")
async def update_remote_url(body: UpdateRemoteUrlBody, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    # 规格 §16 #3：复刻 CONCAT 拼接（内部接口）
    sql = text(
        f"UPDATE open_source_code_file SET remote_url=CONCAT('{body.base_url}','/',file_path) "
        f"WHERE repo_name=:repo_name"
    )
    result = await db.execute(sql, {"repo_name": body.repo_name})
    return {
        "ok": True,
        "message": "remote_url updated",
        "repo_name": body.repo_name,
        "base_url": body.base_url,
        "updated_count": result.rowcount or 0,
    }


@router.get("/open-source/files")
async def list_open_source_files(
    repo_name: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    total = (
        await db.execute(
            select(func.count()).select_from(OpenSourceCodeFile).where(
                OpenSourceCodeFile.repo_name == repo_name
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(OpenSourceCodeFile)
            .where(OpenSourceCodeFile.repo_name == repo_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    records = [
        {
            "id": r.id,
            "repo_name": r.repo_name,
            "file_path": r.file_path,
            "remote_url": r.remote_url,
            "file_size": r.file_size,
        }
        for r in rows
    ]
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
