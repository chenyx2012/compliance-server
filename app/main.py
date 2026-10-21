from __future__ import annotations

"""
合规平台路由网关（API Gateway）。
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from celery import chain
from celery.result import AsyncResult
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_db, init_db
from app.models.file_ingest import FileIngestResult
from app.schemas.scan import ScanRequest
from app.routers import (
    ai_api,
    ai_cache_router,
    code_clone,
    code_tree,
    compliance_sentry,
    csb_download,
    false_positive_classifier,
    oat_rules,
    open_source_code,
    platform_tasks,
    rag_router,
)
from app.services.csb_downloader import CSBDownloader
from app.services.file_ingest import ingest_from_upload, ingest_from_url
from app.services.tasks import MODULES, _run_scan_async, scan_task
from app.tasks.batch_ai_verify_tasks import batch_ai_verify_task
from app.tasks.csv_parse_tasks import parse_csv_and_save
from app.tasks.open_source_tasks import (
    import_open_source_from_task_path2_cpp,
    import_open_source_from_task_path2_java,
    import_open_source_from_task_path2_nvidia,
    import_open_source_from_task_path2_python,
)
from app.tasks.s2_scan_tasks import _update_s2_status_sync
from app.tasks.self_developed_tasks import import_self_developed_from_path1

logger = logging.getLogger(__name__)


class ScanNotifyRequest(BaseModel):
    task_id: str
    status: str = Field(..., pattern="^(success|failed)$")
    result_file_path: Optional[str] = None
    message: Optional[str] = None
    scan_mode: Optional[str] = Field(None, description="pr|version，缺省从 platform_task 读取")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由注册顺序按规格冻结
app.include_router(compliance_sentry.router)
app.include_router(code_tree.router)
app.include_router(open_source_code.router)
app.include_router(csb_download.router)
app.include_router(code_clone.router)
app.include_router(ai_api.router)
app.include_router(false_positive_classifier.router)
app.include_router(ai_cache_router.router)
app.include_router(rag_router.router)
app.include_router(platform_tasks.router)
app.include_router(oat_rules.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.info(
        "request start — method=%s path=%s client=%s",
        request.method,
        request.url.path,
        request.client.host if request.client else "unknown",
    )
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        "request complete — method=%s path=%s status=%d duration=%.2fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


def _normalize_modules(modules: Optional[List[str]]) -> Optional[List[str]]:
    if modules is None:
        return None
    normalized = [m.lower().strip() for m in modules]
    invalid = [m for m in normalized if m not in MODULES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"invalid modules: {invalid}")
    return normalized


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    from app.services.s3_client import health_check as s3_health

    checks: Dict[str, Any] = {"s3": s3_health()}
    # Redis
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        checks["redis"] = {"ok": True}
    except Exception as e:
        checks["redis"] = {"ok": False, "error": str(e)}
    # MySQL（同步探测）
    try:
        from sqlalchemy import text

        from app.core.sync_db import sync_session

        with sync_session() as session:
            session.execute(text("SELECT 1"))
        checks["mysql"] = {"ok": True}
    except Exception as e:
        checks["mysql"] = {"ok": False, "error": str(e)}

    critical_ok = checks["s3"].get("ok") or not checks["s3"].get("configured")
    status = "success" if critical_ok and checks["redis"].get("ok") else "degraded"
    return {
        "status": status,
        "service": settings.app_name,
        "env": settings.env,
        "checks": checks,
    }


@app.post("/scan/sync")
async def scan_sync(req: ScanRequest, request: Request) -> Dict[str, Any]:
    modules = _normalize_modules(req.modules)
    headers = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["authorization"] = auth
    return await _run_scan_async(
        target=req.target, options=req.options, modules=modules, headers=headers or None
    ) | {"target": req.target}


@app.post("/scan/async")
def scan_async(req: ScanRequest) -> Dict[str, Any]:
    modules = _normalize_modules(req.modules)
    async_result = scan_task.apply_async(args=[req.target, req.options, modules])
    return {"request_id": async_result.id, "state": async_result.state}


@app.get("/scan/result/{request_id}")
def scan_result(request_id: str) -> Dict[str, Any]:
    r = AsyncResult(request_id, app=celery_app)
    if r.state in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
        return {"request_id": request_id, "state": r.state}
    if r.state == "FAILURE":
        return {"request_id": request_id, "state": r.state, "error": str(r.result)}
    return {"request_id": request_id, "state": r.state, "result": r.result}


@app.post("/files/ingest")
async def files_ingest(
    source_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    if (source_url is None or not source_url.strip()) and file is None:
        raise HTTPException(status_code=400, detail="either source_url or file is required")
    if source_url is not None and source_url.strip() and file is not None:
        raise HTTPException(status_code=400, detail="only one of source_url or file is allowed")

    if source_url is not None and source_url.strip():
        try:
            meta, root_path = await ingest_from_url(source_url.strip(), timeout_seconds=300)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        assert file is not None
        data = await file.read()
        meta, root_path = await ingest_from_upload(file.filename or "upload.bin", data)

    source_type = meta.get("source", "unknown")
    source_label = meta.get("url") or meta.get("filename") or ""
    s3_status = meta.get("s3_upload", "Unknown")
    row = FileIngestResult(
        source_type=source_type,
        source_label=source_label[:512] if source_label else None,
        meta=meta,
        tree=None,
        s3_upload_status=str(s3_status)[:512] if s3_status else None,
        source_file_path=root_path,
        status=1,
    )
    db.add(row)
    await db.flush()
    ingest_id = str(row.id)
    logger.info("files_ingest done: ingest_id=%s source_type=%s", ingest_id, source_type)
    return {"status": "success", "ingest_id": ingest_id, "meta": meta}


def _notify_fingerprint(task_id: str, status: str, result_file_path: Optional[str]) -> str:
    return f"{task_id}|{status}|{result_file_path or ''}"


def _try_claim_notify_idempotent(fingerprint: str) -> tuple[bool, Optional[str]]:
    """返回 (is_new, error)。Redis 不可用时放行（is_new=True）。先占短锁，成功后再延长。"""
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        ok = r.set(f"scan_notify:{fingerprint}", "processing", nx=True, ex=600)
        return bool(ok), None
    except Exception as e:
        return True, str(e)


def _finalize_notify_idempotent(fingerprint: str, *, ok: bool) -> None:
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        key = f"scan_notify:{fingerprint}"
        if ok:
            r.set(key, "done", ex=7 * 86400)
        else:
            r.delete(key)
    except Exception:
        pass


@app.post("/api/scan/notify")
async def scan_notify(request: ScanNotifyRequest) -> Dict[str, Any]:
    """S2 完成回调：写终态 → 按 scan_mode 后处理（PR 轻量 / version 完整）。

    幂等：相同 fingerprint 直接返回。入队失败且 NOTIFY_REQUIRE_ENQUEUE 时返回 503。
    """
    local_result_path: Optional[str] = None
    download_error: Optional[str] = None
    open_source_tasks_info: Dict[str, Any] = {}
    self_developed_info: Optional[Dict[str, Any]] = None
    infra_warnings: list[str] = []
    enqueue_ok = True
    postprocess_mode = "version"

    fp = _notify_fingerprint(request.task_id, request.status, request.result_file_path)
    is_new, redis_err = _try_claim_notify_idempotent(fp)
    if redis_err:
        infra_warnings.append(f"redis_idempotency: {redis_err}")
    if not is_new:
        return {
            "success": True,
            "message": "duplicate notify ignored",
            "idempotent": True,
            "task_id": request.task_id,
            "status": request.status,
        }

    # 读取平台任务 scan_mode
    try:
        from app.core.sync_db import sync_session
        from app.models.platform_task import PlatformTask, derive_task_status
        from app.core.time_utils import beijing_now

        with sync_session() as session:
            pt = session.query(PlatformTask).filter(PlatformTask.task_id == request.task_id).one_or_none()
            if pt is not None:
                postprocess_mode = (request.scan_mode or pt.scan_mode or "version").lower()
                if pt.notify_fingerprint == fp:
                    return {
                        "success": True,
                        "message": "duplicate notify ignored",
                        "idempotent": True,
                        "task_id": request.task_id,
                        "status": request.status,
                    }
                # 终态先写；fingerprint 在成功完成后再写（避免 503 后无法重试）
                pt.s2_status = request.status
                pt.task_status = derive_task_status(pt)
                pt.updated_at = beijing_now()
            else:
                postprocess_mode = (request.scan_mode or "version").lower()
    except Exception as e:
        logger.warning("scan_notify — platform_task update skipped: %s", e)
        infra_warnings.append(f"s2_status: {e}")
        postprocess_mode = (request.scan_mode or "version").lower()
        try:
            _update_s2_status_sync(request.task_id, request.status)
        except Exception as e2:
            infra_warnings.append(f"s2_status_fallback: {e2}")

    if request.status == "success" and request.result_file_path:
        s3_path = request.result_file_path.lstrip("/")
        bucket_prefix = f"{settings.s3_bucket_name}/"
        if s3_path.startswith(bucket_prefix):
            s3_path = s3_path[len(bucket_prefix) :]

        download_dir = Path(settings.s3_download_path) / request.task_id
        download_dir.mkdir(parents=True, exist_ok=True)

        if Path(request.result_file_path).exists():
            local_result_path = request.result_file_path
        else:
            candidate = download_dir / Path(s3_path).name
            if candidate.exists():
                local_result_path = str(candidate)
            else:
                try:
                    from app.services.s3_client import effective_access_key, effective_secret_key

                    dl = CSBDownloader(
                        app_token=settings.s3_app_token or effective_access_key(),
                        region=settings.s3_region,
                        bucket_name=settings.s3_bucket_name,
                        bucket_id=settings.s3_bucket_id,
                        vendor=settings.s3_vendor,
                        shared=settings.s3_shared,
                        endpoint_url=settings.s3_endpoint_url or None,
                        access_key=settings.s3_access_key or effective_access_key(),
                        secret_key=settings.s3_secret_key or effective_secret_key(),
                    )
                    result = dl.download_single_file(s3_path, str(download_dir))
                    if result.get("success") and result.get("local_path"):
                        local_result_path = result["local_path"]
                    else:
                        download_error = str(result.get("message") or "MinIO/S3 download failed")
                        logger.warning("scan_notify — download failed: %s", download_error)
                except Exception as e:
                    download_error = str(e)
                    logger.exception("scan_notify — download exception: %s", e)

        if local_result_path and Path(local_result_path).exists():
            try:
                parse_q = settings.celery_queue_parse
                import_q = settings.celery_queue_import
                if postprocess_mode == "pr":
                    if settings.pr_postprocess_ai_verify:
                        chain(
                            parse_csv_and_save.s(local_result_path, request.task_id),
                            batch_ai_verify_task.s(request.task_id, True, True, False),
                        ).apply_async(queue=parse_q)
                    else:
                        parse_csv_and_save.apply_async(
                            args=[local_result_path, request.task_id],
                            queue=parse_q,
                        )
                    if settings.pr_postprocess_imports:
                        nvidia_ar = import_open_source_from_task_path2_nvidia.apply_async(
                            args=[request.task_id, "nvidia"], queue=import_q
                        )
                        cpp_ar = import_open_source_from_task_path2_cpp.apply_async(
                            args=[request.task_id, "cpp"], queue=import_q
                        )
                        java_ar = import_open_source_from_task_path2_java.apply_async(
                            args=[request.task_id, "java"], queue=import_q
                        )
                        import_open_source_from_task_path2_python.apply_async(
                            args=[request.task_id, "python"], queue=import_q
                        )
                        self_ar = import_self_developed_from_path1.apply_async(
                            args=[request.task_id], queue=import_q
                        )
                        open_source_tasks_info = {
                            "nvidia": {"task_id": nvidia_ar.id, "repo_name": "nvidia", "status": "submitted"},
                            "cpp": {"task_id": cpp_ar.id, "repo_name": "cpp", "status": "submitted"},
                            "java": {"task_id": java_ar.id, "repo_name": "java", "status": "submitted"},
                        }
                        self_developed_info = {
                            "task_id": self_ar.id,
                            "repo_name": settings.self_developed_repo_name,
                            "status": "submitted",
                        }
                else:
                    chain(
                        parse_csv_and_save.s(local_result_path, request.task_id),
                        batch_ai_verify_task.s(request.task_id, True, True, False),
                    ).apply_async(queue=parse_q)
                    nvidia_ar = import_open_source_from_task_path2_nvidia.apply_async(
                        args=[request.task_id, "nvidia"], queue=import_q
                    )
                    cpp_ar = import_open_source_from_task_path2_cpp.apply_async(
                        args=[request.task_id, "cpp"], queue=import_q
                    )
                    java_ar = import_open_source_from_task_path2_java.apply_async(
                        args=[request.task_id, "java"], queue=import_q
                    )
                    import_open_source_from_task_path2_python.apply_async(
                        args=[request.task_id, "python"], queue=import_q
                    )
                    self_ar = import_self_developed_from_path1.apply_async(
                        args=[request.task_id], queue=import_q
                    )
                    open_source_tasks_info = {
                        "nvidia": {"task_id": nvidia_ar.id, "repo_name": "nvidia", "status": "submitted"},
                        "cpp": {"task_id": cpp_ar.id, "repo_name": "cpp", "status": "submitted"},
                        "java": {"task_id": java_ar.id, "repo_name": "java", "status": "submitted"},
                    }
                    self_developed_info = {
                        "task_id": self_ar.id,
                        "repo_name": settings.self_developed_repo_name,
                        "status": "submitted",
                    }
            except Exception as e:
                enqueue_ok = False
                logger.warning("scan_notify — celery enqueue failed: %s", e)
                infra_warnings.append(f"celery: {e}")
        elif request.status == "success":
            enqueue_ok = False
            logger.error(
                "scan_notify — result CSV not available — task_id=%s err=%s",
                request.task_id,
                download_error,
            )

    # 更新 FileIngestResult + postprocess 状态
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.platform_task import PlatformTask

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(FileIngestResult).where(FileIngestResult.task_id == request.task_id)
            )
            record = result.scalar_one_or_none()
            if record is not None:
                record.status = 1 if request.status == "success" else 2
                meta = dict(record.meta or {})
                meta.update(
                    {
                        "result_file_path": request.result_file_path,
                        "result_local_path": local_result_path,
                        "scan_message": request.message,
                        "postprocess_mode": postprocess_mode,
                    }
                )
                record.meta = meta

            pt_row = (
                await db.execute(select(PlatformTask).where(PlatformTask.task_id == request.task_id))
            ).scalar_one_or_none()
            if pt_row is not None:
                if request.status != "success":
                    pt_row.s2_postprocess_status = "skipped"
                    pt_row.notify_fingerprint = fp
                elif not enqueue_ok:
                    pt_row.s2_postprocess_status = "failed"
                else:
                    pt_row.s2_postprocess_status = "pending"
                    pt_row.notify_fingerprint = fp
            await db.commit()
    except Exception as e:
        logger.warning("scan_notify — db update skipped: %s", e)
        infra_warnings.append(f"db: {e}")

    if (
        settings.notify_require_enqueue
        and request.status == "success"
        and request.result_file_path
        and not enqueue_ok
    ):
        _finalize_notify_idempotent(fp, ok=False)
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "message": "notify accepted status but postprocess enqueue failed; retry",
                "task_id": request.task_id,
                "download_error": download_error,
                "infra_warnings": infra_warnings,
            },
        )

    _finalize_notify_idempotent(fp, ok=True)

    out: Dict[str, Any] = {
        "success": True,
        "message": "通知接收成功",
        "task_id": request.task_id,
        "status": request.status,
        "scan_mode": postprocess_mode,
    }
    if open_source_tasks_info:
        msg = "通知接收成功，已提交CSV解析和开源代码保存任务"
        if self_developed_info:
            msg = "通知接收成功，已提交CSV解析和开源代码保存和自研代码保存任务"
        out["message"] = msg
        out["open_source_tasks"] = open_source_tasks_info
        if self_developed_info:
            out["self_developed_task"] = self_developed_info
    elif postprocess_mode == "pr" and request.status == "success" and enqueue_ok:
        out["message"] = "通知接收成功，PR 轻量后处理已提交（CSV/可选 AI）"
    elif request.status == "success" and request.result_file_path and not local_result_path:
        out["warning"] = "result file download failed; CSV parse not queued"
        out["download_error"] = download_error
    if infra_warnings:
        out["infra_warnings"] = infra_warnings
    return out
