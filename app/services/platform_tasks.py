"""
平台异步任务：向 compliance-sentry 提交 mission/upload 或 mission/git。

鉴权：网关服务账号自动登录获取 token，前端无需传递 Authorization。
Celery task 为同步上下文，使用同步 httpx 调用 sentry 登录接口获取 token。
"""

from __future__ import annotations

import logging
import os
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
    r = httpx.post(url, data=payload, timeout=30.0)
    if not r.is_success:
        raise RuntimeError(f"sentry login failed: HTTP {r.status_code} — {r.text[:300]}")
    body = r.json()
    token = body.get("access_token") or body.get("token")
    if not token:
        raise RuntimeError(f"sentry login response missing access_token: {body}")
    return token


@celery_app.task(bind=True, name="platform.sentry_mission")
def sentry_mission_task(
    self,
    mode: str,
    project_name: str,
    temp_path: Optional[str] = None,
    git_url: Optional[str] = None,
    third_party: bool = False,
    fallback_tree: bool = False,
    branch_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """
    mode: upload | git
    upload 时 temp_path 为本地 zip 路径，任务结束后删除。
    token 由网关服务账号自动获取，无需前端传入。
    """
    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"

    try:
        token = _get_token_sync()
    except RuntimeError as e:
        logger.error("sentry_mission_task: auth failed — %s", e)
        return {"ok": False, "error": f"sentry auth failed: {e}"}

    headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}

    try:
        if mode == "upload":
            if not temp_path or not os.path.isfile(temp_path):
                return {"ok": False, "error": "missing temp file for upload"}
            with open(temp_path, "rb") as f:
                files = {"file": (os.path.basename(temp_path), f, "application/zip")}
                data = {
                    "project_name": project_name,
                    "third_party": str(third_party).lower(),
                    "fallback_tree": str(fallback_tree).lower(),
                }
                r = httpx.post(
                    f"{base}/mission/upload",
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=600.0,
                )
        elif mode == "git":
            if not git_url:
                return {"ok": False, "error": "missing git_url"}
            data: Dict[str, Any] = {
                "project_name": project_name,
                "git_url": git_url,
                "third_party": str(third_party).lower(),
                "fallback_tree": str(fallback_tree).lower(),
            }
            if branch_tag:
                data["branch_tag"] = branch_tag
            r = httpx.post(
                f"{base}/mission/git",
                data=data,
                headers=headers,
                timeout=600.0,
            )
        else:
            return {"ok": False, "error": f"unknown mode: {mode}"}

        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        if r.is_success:
            return {"ok": True, "status_code": r.status_code, "sentry": body}
        return {"ok": False, "status_code": r.status_code, "error": body}

    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if temp_path and os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
