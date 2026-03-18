"""
平台异步任务：向 compliance-sentry 提交 mission/upload 或 mission/git。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from app.core.celery_app import celery_app
from app.core.config import settings


@celery_app.task(bind=True, name="platform.sentry_mission")
def sentry_mission_task(
    self,
    mode: str,
    project_name: str,
    authorization: str,
    temp_path: Optional[str] = None,
    git_url: Optional[str] = None,
    third_party: bool = False,
    fallback_tree: bool = False,
    branch_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """
    mode: upload | git
    upload 时 temp_path 为本地 zip 路径，任务结束后删除。
    """
    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
    headers: Dict[str, str] = {}
    if authorization:
        headers["Authorization"] = authorization
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
                r = httpx.post(f"{base}/mission/upload", files=files, data=data, headers=headers, timeout=600.0)
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
            r = httpx.post(f"{base}/mission/git", data=data, headers=headers, timeout=600.0)
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
