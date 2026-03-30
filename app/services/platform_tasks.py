"""
平台异步任务：向 compliance-sentry 提交 mission/upload 或 mission/git。

鉴权：网关服务账号自动登录获取 token，前端无需传递 Authorization。
Celery task 为同步上下文，使用同步 httpx 调用 sentry 登录接口获取 token。
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from typing import Any, Dict, Optional

import httpx

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_token_sync() -> str:
    """
    同步登录 sentry 获取 token（供 Celery worker 同步上下文使用）。
    失败时抛出 RuntimeError。
    """
    base = settings.compliance_sentry_base_url.rstrip("/")
    url = f"{base}/api/v1/auth/login"
    payload = {
        "username": settings.compliance_sentry_username,
        "password": settings.compliance_sentry_password,
    }
    logger.info("_get_token_sync — url=%s username=%s", url, settings.compliance_sentry_username)
    _proxy = settings.compliance_sentry_proxy or None
    r = httpx.post(url, data=payload, timeout=30.0, proxy=_proxy)
    if not r.is_success:
        logger.error("_get_token_sync failed — url=%s status=%d body=%s", url, r.status_code, r.text[:300])
        raise RuntimeError(f"sentry login failed: HTTP {r.status_code} — {r.text[:300]}")
    body = r.json()
    token = body.get("access_token") or body.get("token")
    if not token:
        logger.error("_get_token_sync — no token in response — body=%s", body)
        raise RuntimeError(f"sentry login response missing access_token: {body}")
    logger.info("_get_token_sync success — token_prefix=%s...", token[:20])
    return token


@celery_app.task(bind=True, name="platform.sentry_mission")
def sentry_mission_task(
    self,
    mode: str,
    task_name: str,
    temp_path: Optional[str] = None,
    git_url: Optional[str] = None,
    third_party: bool = False,
    fallback_tree: bool = False,
    branch_tag: Optional[str] = None,
    temp_shadow_path: Optional[str] = None,
    temp_license_shadow_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    mode: upload | git
    upload 时 temp_path 为本地 zip 路径，任务结束后删除。
    token 由网关服务账号自动获取，无需前端传入。
    """
    task_id = self.request.id
    logger.info(
        "sentry_mission_task start — task_id=%s mode=%s task_name=%s git_url=%s temp_path=%s shadow=%s license_shadow=%s",
        task_id,
        mode,
        task_name,
        git_url,
        bool(temp_path),
        bool(temp_shadow_path),
        bool(temp_license_shadow_path),
    )
    
    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"

    try:
        token = _get_token_sync()
    except RuntimeError as e:
        logger.error("sentry_mission_task auth failed — task_id=%s error=%s", task_id, e)
        return {"status": "error", "error": f"sentry auth failed: {e}"}

    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}

    _proxy = settings.compliance_sentry_proxy or None
    try:
        if mode == "upload":
            if not temp_path or not os.path.isfile(temp_path):
                logger.error(
                    "sentry_mission_task — missing temp file — task_id=%s temp_path=%s",
                    task_id,
                    temp_path,
                )
                return {"status": "error", "error": "missing temp file for upload"}

            data: Dict[str, Any] = {
                "project_name": task_name,
                "third_party": str(third_party).lower(),
                "fallback_tree": str(fallback_tree).lower(),
            }

            logger.info(
                "sentry_mission_task — uploading zip — task_id=%s file=%s size=%d bytes",
                task_id,
                os.path.basename(temp_path),
                os.path.getsize(temp_path),
            )

            with ExitStack() as stack:
                f_main = stack.enter_context(open(temp_path, "rb"))
                files: Dict[str, Any] = {
                    "file": (os.path.basename(temp_path), f_main, "application/zip"),
                }
                if temp_shadow_path and os.path.isfile(temp_shadow_path):
                    f_shadow = stack.enter_context(open(temp_shadow_path, "rb"))
                    files["shadow_file"] = (
                        os.path.basename(temp_shadow_path),
                        f_shadow,
                        "application/octet-stream",
                    )
                if temp_license_shadow_path and os.path.isfile(temp_license_shadow_path):
                    f_license = stack.enter_context(open(temp_license_shadow_path, "rb"))
                    files["license_shadow"] = (
                        os.path.basename(temp_license_shadow_path),
                        f_license,
                        "application/octet-stream",
                    )

                r = httpx.post(
                    f"{base}/mission/upload",
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=600.0,
                    proxy=_proxy,
                )
        elif mode == "git":
            if not git_url:
                logger.error("sentry_mission_task — missing git_url — task_id=%s", task_id)
                return {"status": "error", "error": "missing git_url"}
            
            logger.info("sentry_mission_task — submitting git — task_id=%s git_url=%s", task_id, git_url)
            
            data = {
                "project_name": task_name,
                "git_url": git_url,
                "third_party": str(third_party).lower(),
                "fallback_tree": str(fallback_tree).lower(),
            }
            if branch_tag:
                data["branch_tag"] = branch_tag

            files: Optional[Dict[str, Any]] = None
            if (temp_shadow_path and os.path.isfile(temp_shadow_path)) or (
                temp_license_shadow_path and os.path.isfile(temp_license_shadow_path)
            ):
                with ExitStack() as stack:
                    files = {}
                    if temp_shadow_path and os.path.isfile(temp_shadow_path):
                        f_shadow = stack.enter_context(open(temp_shadow_path, "rb"))
                        files["shadow_file"] = (
                            os.path.basename(temp_shadow_path),
                            f_shadow,
                            "application/octet-stream",
                        )
                    if temp_license_shadow_path and os.path.isfile(temp_license_shadow_path):
                        f_license = stack.enter_context(open(temp_license_shadow_path, "rb"))
                        files["license_shadow"] = (
                            os.path.basename(temp_license_shadow_path),
                            f_license,
                            "application/octet-stream",
                        )

                    r = httpx.post(
                        f"{base}/mission/git",
                        data=data,
                        files=files,
                        headers=headers,
                        timeout=600.0,
                        proxy=_proxy,
                    )
            else:
                r = httpx.post(
                    f"{base}/mission/git",
                    data=data,
                    headers=headers,
                    timeout=600.0,
                    proxy=_proxy,
                )
        else:
            logger.error("sentry_mission_task — unknown mode — task_id=%s mode=%s", task_id, mode)
            return {"status": "error", "error": f"unknown mode: {mode}"}

        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        
        if r.is_success:
            logger.info(
                "sentry_mission_task success — task_id=%s mode=%s status=%d analysis_id=%s",
                task_id, mode, r.status_code, body.get("analysis_id", "N/A")
            )
            return {"status": "success", "status_code": r.status_code, "sentry": body}
        
        logger.error(
            "sentry_mission_task failed — task_id=%s mode=%s status=%d body=%s",
            task_id, mode, r.status_code, str(body)[:500]
        )
        return {"status": "error", "status_code": r.status_code, "error": body}

    except Exception as e:
        logger.error("sentry_mission_task exception — task_id=%s mode=%s error=%s", task_id, mode, str(e))
        return {"status": "error", "error": str(e)}
    finally:
        if temp_path and os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
                logger.info("sentry_mission_task — temp file deleted — task_id=%s path=%s", task_id, temp_path)
            except OSError as e:
                logger.warning("sentry_mission_task — failed to delete temp file — task_id=%s path=%s error=%s", 
                              task_id, temp_path, e)
        if temp_shadow_path and os.path.isfile(temp_shadow_path):
            try:
                os.unlink(temp_shadow_path)
                logger.info(
                    "sentry_mission_task — temp shadow deleted — task_id=%s path=%s",
                    task_id,
                    temp_shadow_path,
                )
            except OSError as e:
                logger.warning(
                    "sentry_mission_task — failed to delete temp shadow — task_id=%s path=%s error=%s",
                    task_id,
                    temp_shadow_path,
                    e,
                )
        if temp_license_shadow_path and os.path.isfile(temp_license_shadow_path):
            try:
                os.unlink(temp_license_shadow_path)
                logger.info(
                    "sentry_mission_task — temp license_shadow deleted — task_id=%s path=%s",
                    task_id,
                    temp_license_shadow_path,
                )
            except OSError as e:
                logger.warning(
                    "sentry_mission_task — failed to delete temp license_shadow — task_id=%s path=%s error=%s",
                    task_id,
                    temp_license_shadow_path,
                    e,
                )
