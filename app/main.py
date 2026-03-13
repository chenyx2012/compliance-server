from __future__ import annotations

"""
合规平台路由网关（API Gateway）。

职责：
- 面向前端提供统一的 HTTP API；
- 并发调用 4 个可独立部署的扫描模块服务（A/B/C/D）并聚合结果；
- 对于耗时/高并发场景，将请求投递到队列（Celery+Redis），前端用 request_id 轮询结果。
"""

from typing import Any, Dict, List, Optional

from celery.result import AsyncResult
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile

from app.core.celery_app import celery_app
from app.core.config import settings
from app.schemas.scan import ScanRequest
from app.services.file_ingest import ingest_from_upload, ingest_from_url
from app.services.tasks import MODULES, _run_scan_async, scan_task


app = FastAPI(title=settings.app_name)


def _normalize_modules(modules: Optional[List[str]]) -> Optional[List[str]]:
    """校验并标准化模块列表（a/b/c/d）。None 表示默认全扫。"""
    if modules is None:
        return None
    normalized = [m.lower().strip() for m in modules]
    invalid = [m for m in normalized if m not in MODULES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"invalid modules: {invalid}, allowed: {sorted(MODULES.keys())}")
    return normalized


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    """K8s/负载均衡健康检查探针。"""
    return {"ok": True, "service": settings.app_name, "env": settings.env}


@app.post("/scan/sync")
async def scan_sync(req: ScanRequest, request: Request) -> Dict[str, Any]:
    """
    同步聚合扫描：网关在一次请求内并发调用下游模块并返回结果。

    适用于：扫描耗时可控、前端需要立即展示结果的场景。
    """
    modules = _normalize_modules(req.modules)
    headers = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["authorization"] = auth
    return await _run_scan_async(target=req.target, options=req.options, modules=modules, headers=headers or None) | {"target": req.target}


@app.post("/scan/async")
def scan_async(req: ScanRequest) -> Dict[str, Any]:
    """
    异步队列扫描：将扫描请求投递到 Celery 队列，立即返回 request_id（Celery task id）。

    适用于：高并发/耗时扫描，避免占用 API 连接与工作线程。
    """
    modules = _normalize_modules(req.modules)
    async_result = scan_task.apply_async(args=[req.target, req.options, modules])
    return {"request_id": async_result.id, "state": async_result.state}


@app.get("/scan/result/{request_id}")
def scan_result(request_id: str) -> Dict[str, Any]:
    """查询异步任务状态/结果。结果存储由 Celery backend（此处 Redis）承载。"""
    r = AsyncResult(request_id, app=celery_app)
    if r.state in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
        return {"request_id": request_id, "state": r.state}
    if r.state == "FAILURE":
        return {"request_id": request_id, "state": r.state, "error": str(r.result)}
    return {"request_id": request_id, "state": r.state, "result": r.result}


@app.post("/files/ingest")
async def files_ingest(
    source_url: Optional[str] = Form(default=None, description="文件/压缩包 URL（支持 github/gitee/gitcode 的 blob 链接）"),
    file: Optional[UploadFile] = File(default=None, description="上传文件/压缩包（zip/tar/tar.gz/tgz 或普通文件）"),
) -> Dict[str, Any]:
    """
    获取文件并解析为目录树。

    输出节点结构：
    - path: str
    - next: { name: node, ... }
    - content: None | JSON（叶子文件内容）
    """
    if (source_url is None or not source_url.strip()) and file is None:
        raise HTTPException(status_code=400, detail="either source_url or file is required")
    if source_url is not None and source_url.strip() and file is not None:
        raise HTTPException(status_code=400, detail="only one of source_url or file is allowed")

    if source_url is not None and source_url.strip():
        tree, meta = await ingest_from_url(source_url.strip(), timeout_seconds=60.0)
        return {"ok": True, "meta": meta, "tree": tree}

    assert file is not None
    data = await file.read()
    tree, meta = await ingest_from_upload(file.filename or "upload.bin", data)
    return {"ok": True, "meta": meta, "tree": tree}

