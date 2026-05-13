"""
平台异步任务：向 compliance-sentry 提交 mission/upload 或 mission/git。

鉴权：网关服务账号自动登录获取 token，前端无需传递 Authorization。
Celery task 为同步上下文，使用同步 httpx 调用 sentry 登录接口获取 token。

任务流水线：
  sentry_mission_task  — 提交文件给 sentry，获得 analysis_id，然后 chain 启动
  sentry_poll_task     — 轮询 GET /analysis/{analysis_id}/status，完成后写库更新 s3_status
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

# 轮询间隔（秒）和最大轮询次数（默认 120 次 × 10s = 20 分钟）
_POLL_INTERVAL = 60
_POLL_MAX_RETRIES = 120


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
    platform_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    mode: upload | git
    upload 时 temp_path 为本地 zip 路径，任务结束后删除。
    token 由网关服务账号自动获取，无需前端传入。
    提交成功后，若传入 platform_task_id，自动 chain 启动 sentry_poll_task 轮询扫描状态。
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
            analysis_id = body.get("analysis_id")
            logger.info(
                "sentry_mission_task success — task_id=%s mode=%s status=%d analysis_id=%s",
                task_id, mode, r.status_code, analysis_id or "N/A"
            )
            # 提交成功后启动轮询任务跟踪扫描状态
            if platform_task_id and analysis_id:
                # 先同步写入 analysis_id + running 状态
                _update_platform_task_s3_sync(platform_task_id, "running", analysis_id)
                # 延迟 _POLL_INTERVAL 秒后开始首次轮询
                sentry_poll_task.apply_async(
                    kwargs={
                        "analysis_id": analysis_id,
                        "platform_task_id": platform_task_id,
                    },
                    countdown=_POLL_INTERVAL,
                )
                logger.info(
                    "sentry_mission_task — poll task scheduled — analysis_id=%s platform_task_id=%s",
                    analysis_id, platform_task_id,
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


# ---------------------------------------------------------------------------
# 轮询任务：跟踪 sentry 异步扫描完成后更新主任务 s3_status
# ---------------------------------------------------------------------------

def _update_platform_task_s3_sync(
    platform_task_id: str,
    s3_status: str,
    analysis_id: Optional[str] = None,
    has_conflicts: Optional[bool] = None,
    conflict_count: int = 0,
) -> None:
    """
    同步（Celery worker 上下文）更新 platform_task 的 s3_status 和 task_status。
    使用 SQLAlchemy 同步引擎，避免在 Celery 中引入 asyncio。

    has_conflicts / conflict_count：扫描完成时从 sentry conflicts 接口获取后传入；
    不传（默认 None）时不覆盖 DB 中已有值。
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.models.platform_task import PlatformTask, derive_task_status

    # 将 aiomysql async URL 转为 pymysql 同步 URL
    db_url = settings.database_url
    sync_url = db_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    engine = create_engine(sync_url, pool_pre_ping=True, pool_recycle=3600)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with Session() as session:
            pt = session.execute(
                select(PlatformTask).where(PlatformTask.task_id == platform_task_id)
            ).scalar_one_or_none()
            if pt is None:
                logger.warning(
                    "_update_platform_task_s3_sync — platform_task_id=%s not found",
                    platform_task_id,
                )
                return
            pt.s3_status = s3_status
            if analysis_id:
                pt.s3_analysis_id = analysis_id
            if has_conflicts is not None:
                pt.s3_has_conflicts = has_conflicts
                pt.s3_conflict_count = conflict_count
            pt.task_status = derive_task_status(pt)
            pt.updated_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(
                "_update_platform_task_s3_sync — platform_task_id=%s s3_status=%s "
                "has_conflicts=%s conflict_count=%d task_status=%s",
                platform_task_id, s3_status,
                has_conflicts, conflict_count, pt.task_status,
            )
    except Exception as exc:
        logger.error(
            "_update_platform_task_s3_sync — failed platform_task_id=%s: %s",
            platform_task_id,
            exc,
        )
    finally:
        engine.dispose()


@celery_app.task(
    bind=True,
    name="platform.sentry_poll",
    max_retries=_POLL_MAX_RETRIES,
)
def sentry_poll_task(
    self,
    analysis_id: str,
    platform_task_id: str,
) -> Dict[str, Any]:
    """
    轮询 GET /analysis/{analysis_id}/status，直到 sentry 扫描完成。

    - 每 _POLL_INTERVAL 秒轮询一次，最多 _POLL_MAX_RETRIES 次
    - sentry current_status: pending / running → 继续重试
    - completed                                 → s3_status=success，写库
    - failed / terminated                       → s3_status=failed，写库
    - 超出重试次数                              → s3_status=failed（超时），写库
    """
    task_id = self.request.id
    logger.info(
        "sentry_poll_task — analysis_id=%s platform_task_id=%s attempt=%d/%d",
        analysis_id,
        platform_task_id,
        self.request.retries + 1,
        _POLL_MAX_RETRIES,
    )

    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
    _proxy = settings.compliance_sentry_proxy or None

    try:
        token = _get_token_sync()
    except RuntimeError as e:
        logger.error(
            "sentry_poll_task — auth failed — task_id=%s analysis_id=%s error=%s",
            task_id, analysis_id, e,
        )
        # 认证失败时稍后重试，不直接标记失败
        raise self.retry(exc=e, countdown=_POLL_INTERVAL)

    try:
        r = httpx.get(
            f"{base}/analysis/{analysis_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
            proxy=_proxy,
        )
    except Exception as exc:
        logger.warning(
            "sentry_poll_task — request error, will retry — analysis_id=%s error=%s",
            analysis_id, exc,
        )
        raise self.retry(exc=exc, countdown=_POLL_INTERVAL)

    if not r.is_success:
        logger.warning(
            "sentry_poll_task — non-2xx from sentry — analysis_id=%s status=%d body=%s",
            analysis_id, r.status_code, r.text[:200],
        )
        raise self.retry(exc=Exception(f"HTTP {r.status_code}"), countdown=_POLL_INTERVAL)

    try:
        body = r.json()
    except Exception:
        raise self.retry(exc=Exception("invalid JSON"), countdown=_POLL_INTERVAL)

    data = body.get("data") or body
    current_status = (data.get("current_status") or "").lower()
    progress = data.get("progress", 0)

    logger.info(
        "sentry_poll_task — analysis_id=%s current_status=%s progress=%s",
        analysis_id, current_status, progress,
    )

    # 终态处理：completed → success，顺带拉取冲突数
    if current_status == "completed":
        has_conflicts: Optional[bool] = None
        conflict_count: int = 0
        try:
            rc = httpx.get(
                f"{base}/analysis/{analysis_id}/conflicts",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
                proxy=_proxy,
            )
            if rc.is_success:
                conflicts_body = rc.json()
                conflict_list = conflicts_body.get("conflicts", [])
                conflict_count = len(conflict_list)
                has_conflicts = conflict_count > 0
                logger.info(
                    "sentry_poll_task — fetched conflicts — analysis_id=%s count=%d",
                    analysis_id, conflict_count,
                )
            else:
                logger.warning(
                    "sentry_poll_task — conflicts endpoint returned %d, skipping — analysis_id=%s",
                    rc.status_code, analysis_id,
                )
        except Exception as ce:
            logger.warning(
                "sentry_poll_task — failed to fetch conflicts, will store without — "
                "analysis_id=%s error=%s",
                analysis_id, ce,
            )
        try:
            _update_platform_task_s3_sync(
                platform_task_id, "success", analysis_id,
                has_conflicts=has_conflicts,
                conflict_count=conflict_count,
            )
        except Exception as db_exc:
            logger.error(
                "sentry_poll_task — DB update failed for completed status, will retry — "
                "analysis_id=%s platform_task_id=%s error=%s",
                analysis_id, platform_task_id, db_exc,
            )
            raise self.retry(exc=db_exc, countdown=_POLL_INTERVAL)
        return {
            "status": "success",
            "analysis_id": analysis_id,
            "platform_task_id": platform_task_id,
            "current_status": current_status,
            "conflict_count": conflict_count,
        }

    # 终态处理：failed / terminated → failed
    if current_status in ("failed", "terminated"):
        try:
            _update_platform_task_s3_sync(platform_task_id, "failed", analysis_id)
        except Exception as db_exc:
            logger.error(
                "sentry_poll_task — DB update failed for failed/terminated status, will retry — "
                "analysis_id=%s platform_task_id=%s error=%s",
                analysis_id, platform_task_id, db_exc,
            )
            raise self.retry(exc=db_exc, countdown=_POLL_INTERVAL)
        return {
            "status": "failed",
            "analysis_id": analysis_id,
            "platform_task_id": platform_task_id,
            "current_status": current_status,
        }

    # 仍在 pending / running → 继续轮询
    if self.request.retries >= _POLL_MAX_RETRIES - 1:
        logger.error(
            "sentry_poll_task — polling timeout — analysis_id=%s platform_task_id=%s",
            analysis_id, platform_task_id,
        )
        try:
            _update_platform_task_s3_sync(platform_task_id, "failed", analysis_id)
        except Exception as db_exc:
            logger.error(
                "sentry_poll_task — DB update failed on timeout — "
                "analysis_id=%s platform_task_id=%s error=%s",
                analysis_id, platform_task_id, db_exc,
            )
        return {
            "status": "timeout",
            "analysis_id": analysis_id,
            "platform_task_id": platform_task_id,
        }

    raise self.retry(countdown=_POLL_INTERVAL)
