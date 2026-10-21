from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.sync_db import sync_session
from app.core.time_utils import beijing_now

logger = logging.getLogger(__name__)


def _update_s2_status_sync(platform_task_id: str, s2_status: str) -> None:
    from app.models.platform_task import PlatformTask, derive_task_status

    try:
        with sync_session() as session:
            pt = session.query(PlatformTask).filter(PlatformTask.task_id == platform_task_id).one_or_none()
            if pt is None:
                logger.warning("_update_s2_status_sync — task_id=%s not found", platform_task_id)
                return
            # 终态 success 仅由 notify 写入；此处禁止用 success 覆盖 failed
            if s2_status == "success" and pt.s2_status == "failed":
                logger.info("skip success overwrite after failed — task_id=%s", platform_task_id)
                return
            pt.s2_status = s2_status
            pt.task_status = derive_task_status(pt)
            pt.updated_at = beijing_now()
    except Exception as e:
        logger.error("_update_s2_status_sync error: %s", e)


def build_s2_scan_payload(
    *,
    task_id: str,
    download_path: str,
    upload_path: str,
    language: str = "c",
    stage: str = "scan",
    sync: bool = False,
    scan_mode: str = "version",
) -> Dict[str, Any]:
    """构造 AICodeScanService /api/scan/process 契约字段（以 download_path/upload_path 为准）。"""
    bucket = settings.effective_s3_uploader_bucket_name
    raw = (download_path or "").strip().lstrip("/")
    # 去掉误加的尾斜杠（目录式），保留 .zip
    if raw.endswith("/") and not raw.lower().endswith(".zip/"):
        raw = raw.rstrip("/")
    elif raw.endswith(".zip/"):
        raw = raw[:-1]

    s3_key = raw
    prefix = f"{bucket}/"
    if s3_key.startswith(prefix):
        s3_key = s3_key[len(prefix) :]

    # 规范 download_path = bucket/key
    if raw.startswith(prefix) or raw.startswith(f"{bucket}/"):
        normalized_download = raw if not raw.endswith("/") else raw.rstrip("/")
    else:
        normalized_download = f"{bucket}/{s3_key}"

    return {
        "task_id": task_id,
        "download_path": normalized_download,
        "upload_path": upload_path,
        "sync": sync,
        "scan_mode": scan_mode,
        # 兼容旧字段（扫描侧可忽略）
        "s3_bucket": bucket,
        "s3_key": s3_key,
        "language": language,
        "custom_model": None,
        "stage": stage,
        "topk": 1,
        "threshold": 0.8,
        "s3_endpoint_url": settings.s3_endpoint_url or None,
        "s3_region": settings.s3_region,
    }


def resolve_s2_queue(scan_mode: str) -> str:
    mode = (scan_mode or "version").lower()
    if mode == "pr":
        return settings.celery_queue_scan_pr
    return settings.celery_queue_scan_version


@celery_app.task(bind=True, name="platform_tasks.s2_scan")
def s2_scan_task(
    self,
    task_id: str,
    download_path: str,
    upload_path: str,
    scan_payload: Optional[Dict[str, Any]] = None,
    scan_mode: str = "version",
) -> Dict[str, Any]:
    """投递 AICodeScanService /api/scan/process。

    - 202 accepted：保持 running，终态由 notify 写入
    - 200 + success=false：标 failed
    - 200 + success=true（同步模式）：不写 success，仍由 notify 定终态
    - 非 2xx / 异常：标 failed
    """
    _update_s2_status_sync(task_id, "running")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)

    payload = scan_payload or build_s2_scan_payload(
        task_id=task_id,
        download_path=download_path,
        upload_path=upload_path,
        scan_mode=scan_mode,
    )
    request_url = settings.ai_code_scan_service_url.rstrip("/") + "/api/scan/process"
    # 异步受理用短超时；同步扫描用长超时
    want_sync = bool(payload.get("sync"))
    timeout = httpx.Timeout(2592000.0 if want_sync else float(settings.s2_accept_timeout_seconds))
    logger.info(
        "s2_scan_task start — task_id=%s url=%s sync=%s keys=%s",
        task_id,
        request_url,
        want_sync,
        list(payload.keys()),
    )
    try:
        with httpx.Client(timeout=timeout, proxy=None, trust_env=False) as client:
            response = client.post(request_url, json=payload)
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text[:2000]}

        if response.status_code == 202:
            return {
                "success": True,
                "accepted": True,
                "status_code": 202,
                "body": body,
                "message": "S2 scan accepted; awaiting notify",
            }

        if response.is_success:
            if body.get("success") is False:
                _update_s2_status_sync(task_id, "failed")
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "body": body,
                    "message": "S2 scan reported failure",
                }
            # success=true：终态交给 notify，此处保持 running
            return {
                "success": True,
                "accepted": True,
                "status_code": response.status_code,
                "body": body,
                "message": "S2 HTTP ok; terminal status via notify",
            }

        _update_s2_status_sync(task_id, "failed")
        return {
            "success": False,
            "status_code": response.status_code,
            "body": body if isinstance(body, dict) else {"error": "AICodeScanService request failed"},
            "message": f"S2 scan failed with status {response.status_code}",
        }
    except Exception:
        _update_s2_status_sync(task_id, "failed")
        raise
