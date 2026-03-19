"""
compliance-sentry 自动鉴权模块。

网关代表前端持有一个服务账号（COMPLIANCE_SENTRY_USERNAME / COMPLIANCE_SENTRY_PASSWORD），
在调用 sentry 任何需要鉴权的接口前自动获取 token 并缓存，token 失效时自动重新登录。

前端无需传递任何 Authorization 头，鉴权对前端完全透明。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内存缓存（进程级单例）
# ---------------------------------------------------------------------------
_token: Optional[str] = None          # 当前有效 token
_token_expires_at: float = 0.0        # 过期时间戳（Unix 秒），提前 60s 视为过期
_token_lock = asyncio.Lock()           # 防止并发时重复登录

_TOKEN_MARGIN_SECONDS = 60            # 提前多少秒视为即将过期，触发预刷新
_LOGIN_TIMEOUT = httpx.Timeout(read=30.0, connect=10.0, write=10.0, pool=10.0)


async def _do_login() -> tuple[str, float]:
    """
    向 sentry 发起登录请求，返回 (access_token, expires_in_seconds)。
    登录失败时抛出 RuntimeError。
    """
    base = settings.compliance_sentry_base_url.rstrip("/")
    url = f"{base}/api/v1/auth/login"
    payload = {
        "username": settings.compliance_sentry_username,
        "password": settings.compliance_sentry_password,
    }
    logger.info("sentry_auth: logging in — url=%s username=%s", url, settings.compliance_sentry_username)
    try:
        async with httpx.AsyncClient(timeout=_LOGIN_TIMEOUT) as client:
            r = await client.post(url, data=payload)
    except httpx.ConnectTimeout:
        logger.error("sentry_auth: connect timeout — url=%s (connect limit %.1fs)", url, _LOGIN_TIMEOUT.connect)
        raise RuntimeError(f"sentry login connect timeout: {url}")
    except httpx.ConnectError as e:
        logger.error("sentry_auth: connect error — url=%s error=%s", url, e)
        raise RuntimeError(f"sentry login connect error ({url}): {e}")
    except httpx.ReadTimeout:
        logger.error("sentry_auth: read timeout — url=%s (read limit %.1fs)", url, _LOGIN_TIMEOUT.read)
        raise RuntimeError(f"sentry login read timeout: {url}")
    except httpx.RequestError as e:
        logger.error("sentry_auth: request error — url=%s error=%s", url, e)
        raise RuntimeError(f"sentry login request error ({url}): {e}")

    if not r.is_success:
        logger.error(
            "sentry_auth: login failed — url=%s HTTP %s body=%s",
            url, r.status_code, r.text[:500],
        )
        raise RuntimeError(
            f"sentry login failed: HTTP {r.status_code} — {r.text[:300]}"
        )

    try:
        body = r.json()
    except Exception as e:
        logger.error("sentry_auth: failed to parse login response — %s — body=%s", e, r.text[:300])
        raise RuntimeError(f"sentry login response is not valid JSON: {e}")

    token = body.get("access_token") or body.get("token")
    if not token:
        logger.error("sentry_auth: no token in response — body=%s", body)
        raise RuntimeError(f"sentry login response missing access_token: {body}")

    expires_in: float = float(body.get("expires_in") or 25 * 60)
    logger.info("sentry_auth: login successful, token expires in %.0fs", expires_in)
    return token, expires_in


async def get_token(*, force_refresh: bool = False) -> str:
    """
    获取有效的 sentry token（内存缓存，过期自动刷新）。

    - 首次调用：登录并缓存
    - token 距过期不足 60s：提前刷新
    - force_refresh=True：强制重新登录（用于 401 响应后的重试）
    """
    global _token, _token_expires_at

    now = time.monotonic()
    # 快速路径：token 有效且不需要强制刷新，直接返回（不加锁，性能优先）
    if not force_refresh and _token and now < _token_expires_at - _TOKEN_MARGIN_SECONDS:
        return _token

    # 慢速路径：需要登录或刷新，加锁防止并发重复登录
    async with _token_lock:
        # 再次检查，可能在等锁期间已被其他协程刷新
        now = time.monotonic()
        if not force_refresh and _token and now < _token_expires_at - _TOKEN_MARGIN_SECONDS:
            return _token

        new_token, expires_in = await _do_login()
        _token = new_token
        _token_expires_at = time.monotonic() + expires_in
        return _token


def get_auth_header(token: str) -> dict[str, str]:
    """返回携带 token 的 Authorization 请求头字典。"""
    return {"Authorization": f"Bearer {token}"}
