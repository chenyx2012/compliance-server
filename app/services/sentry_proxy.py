"""
将请求原样转发到 compliance-sentry-main 的 /api/v1/*。

鉴权策略：
- 网关持有服务账号（COMPLIANCE_SENTRY_USERNAME / COMPLIANCE_SENTRY_PASSWORD）
- 每次代理请求前自动从 sentry_auth 获取有效 token 并注入 Authorization 头
- 若 sentry 返回 401，自动强制刷新 token 后重试一次
- 前端无需传递任何 Authorization 头
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Union

import httpx
from fastapi import Request, Response

from app.core.config import settings
from app.services.sentry_auth import get_auth_header, get_token

logger = logging.getLogger(__name__)

# 需要格式化的时间字段名（以这些结尾的字段会被检测）
TIME_FIELD_SUFFIXES = ("_at", "_time", "timestamp", "created_by", "updated_by")


def _format_datetime(iso_str: str) -> str:
    """
    将 ISO 8601 时间字符串转换为 yyyy-MM-dd HH:mm:ss 格式。
    支持格式：2026-03-01T10:00:00Z、2026-03-01T10:00:00.123456Z、2026-03-01T10:00:00+08:00 等。
    """
    if not isinstance(iso_str, str):
        return iso_str
    
    # 匹配 ISO 8601 格式（宽松匹配）
    iso_pattern = r"^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}"
    if not re.match(iso_pattern, iso_str):
        return iso_str
    
    try:
        # 尝试解析多种 ISO 8601 格式
        for fmt in [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S",
        ]:
            try:
                dt = datetime.strptime(iso_str.replace("+00:00", "Z"), fmt.replace("%z", "Z") if "Z" in iso_str else fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        
        # 如果上述格式都不匹配，尝试 fromisoformat（Python 3.7+）
        iso_clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_clean)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.debug("failed to parse datetime: %s — %s", iso_str, e)
        return iso_str


def _format_response_times(data: Any) -> Any:
    """
    递归格式化响应体中的所有时间字段。
    """
    if isinstance(data, dict):
        return {k: _format_response_times(v) if not _is_time_field(k) else _format_datetime(v) if isinstance(v, str) else v for k, v in data.items()}
    elif isinstance(data, list):
        return [_format_response_times(item) for item in data]
    else:
        return data


def _is_time_field(key: str) -> bool:
    """判断字段名是否为时间字段。"""
    key_lower = key.lower()
    return any(key_lower.endswith(suffix) for suffix in TIME_FIELD_SUFFIXES)


def _forward_headers(request: Request) -> Dict[str, str]:
    """
    过滤并转发请求头。
    移除 host / content-length 等逐跳头，同时移除前端可能携带的 authorization，
    由网关统一注入服务账号 token。
    """
    skip = {"host", "content-length", "connection", "transfer-encoding", "authorization"}
    return {k: v for k, v in request.headers.items() if k.lower() not in skip}


def _error_response(status_code: int, reason: str, detail: str) -> Response:
    body = json.dumps({"error": reason, "detail": detail}, ensure_ascii=False)
    return Response(
        content=body.encode(),
        status_code=status_code,
        headers={"content-type": "application/json"},
    )


async def _do_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: bytes,
    base_headers: Dict[str, str],
    token: str,
) -> httpx.Response:
    """发起一次带 token 的 HTTP 请求。"""
    headers = {**base_headers, **get_auth_header(token)}
    return await client.request(
        method,
        url,
        content=body if body else None,
        headers=headers,
    )


async def proxy_to_sentry(base_url: str, path: str, request: Request) -> Response:
    """
    透传请求到 sentry，自动处理 token 获取与 401 刷新重试。

    超时策略：
    - connect: 10s  — 连不上时快速失败
    - read:   120s  — 等待 sentry 普通接口响应
    - write:   30s  — 上传请求体
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/v1/{path}"
    query = str(request.url.query)
    if query:
        url = f"{url}?{query}"

    body = await request.body()
    base_headers = _forward_headers(request)
    timeout = httpx.Timeout(read=120.0, connect=10.0, write=30.0, pool=10.0)
    proxy = settings.compliance_sentry_proxy or None

    logger.info("proxy → %s %s (content_length=%d)", request.method, url, len(body))

    try:
        token = await get_token()
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=proxy) as client:
            r = await _do_request(client, request.method, url, body, base_headers, token)

            # sentry 返回 401：token 可能已失效，强制刷新后重试一次
            if r.status_code == 401:
                logger.warning("proxy got 401, refreshing token and retrying: %s", url)
                token = await get_token(force_refresh=True)
                r = await _do_request(client, request.method, url, body, base_headers, token)

    except httpx.ConnectTimeout:
        logger.warning("proxy connect timeout: %s", url)
        return _error_response(
            503,
            "sentry_connect_timeout",
            f"无法连接到 compliance-sentry（{base_url}），请确认服务已启动且 COMPLIANCE_SENTRY_BASE_URL 配置正确",
        )
    except httpx.ConnectError as e:
        logger.warning("proxy connect error: %s — %s", url, e)
        return _error_response(
            503,
            "sentry_connect_error",
            f"连接 compliance-sentry 失败（{base_url}）：{e}，请检查服务是否运行、端口是否正确",
        )
    except httpx.ReadTimeout:
        logger.warning("proxy read timeout: %s", url)
        return _error_response(
            504,
            "sentry_read_timeout",
            f"compliance-sentry 响应超时（{url}），请稍后重试或改用 async_scan=true",
        )
    except httpx.TimeoutException as e:
        logger.warning("proxy timeout: %s — %s", url, e)
        return _error_response(504, "sentry_timeout", str(e))
    except httpx.RequestError as e:
        logger.error("proxy request error: %s — %s", url, e)
        return _error_response(502, "sentry_request_error", str(e))
    except RuntimeError as e:
        # get_token() 登录失败
        logger.error("sentry auth failed: %s", e)
        return _error_response(503, "sentry_auth_failed", str(e))

    out_headers: Dict[str, str] = {}
    for k in ("content-type", "content-disposition"):
        if k in r.headers:
            out_headers[k] = r.headers[k]

    logger.info("proxy ← %s %s (status=%d content_length=%d)", request.method, url, r.status_code, len(r.content))
    
    # 格式化响应中的时间字段
    if "application/json" in r.headers.get("content-type", ""):
        try:
            data = r.json()
            data = _format_response_times(data)
            content = json.dumps(data, ensure_ascii=False).encode("utf-8")
            out_headers["content-type"] = "application/json; charset=utf-8"
            return Response(content=content, status_code=r.status_code, headers=out_headers)
        except Exception as e:
            logger.warning("failed to format response times: %s — returning original", e)
    
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)


async def proxy_to_sentry_noauth(base_url: str, path: str, request: Request) -> Response:
    """
    透传请求到 sentry，不注入网关服务账号 token。

    适用于 auth 类接口（login / register / change-password），
    前端自行携带凭据或 Authorization 头，网关仅做透传。
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/v1/{path}"
    query = str(request.url.query)
    if query:
        url = f"{url}?{query}"

    body = await request.body()
    # auth 接口允许透传前端的 Authorization 头（如 change-password 需要已登录 token）
    skip = {"host", "content-length", "connection", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    timeout = httpx.Timeout(read=30.0, connect=10.0, write=10.0, pool=10.0)
    proxy = settings.compliance_sentry_proxy or None

    logger.info("proxy(noauth) → %s %s (content_length=%d)", request.method, url, len(body))

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=proxy) as client:
            r = await client.request(
                request.method,
                url,
                content=body if body else None,
                headers=headers,
            )
    except httpx.ConnectTimeout:
        logger.warning("proxy(noauth) connect timeout: %s", url)
        return _error_response(
            503,
            "sentry_connect_timeout",
            f"无法连接到 compliance-sentry（{base_url}），请确认服务已启动且 COMPLIANCE_SENTRY_BASE_URL 配置正确",
        )
    except httpx.ConnectError as e:
        logger.warning("proxy(noauth) connect error: %s — %s", url, e)
        return _error_response(
            503,
            "sentry_connect_error",
            f"连接 compliance-sentry 失败（{base_url}）：{e}，请检查服务是否运行、端口是否正确",
        )
    except httpx.ReadTimeout:
        logger.warning("proxy(noauth) read timeout: %s", url)
        return _error_response(504, "sentry_read_timeout", f"compliance-sentry 响应超时（{url}）")
    except httpx.TimeoutException as e:
        logger.warning("proxy(noauth) timeout: %s — %s", url, e)
        return _error_response(504, "sentry_timeout", str(e))
    except httpx.RequestError as e:
        logger.error("proxy(noauth) request error: %s — %s", url, e)
        return _error_response(502, "sentry_request_error", str(e))

    out_headers: Dict[str, str] = {}
    for k in ("content-type", "content-disposition"):
        if k in r.headers:
            out_headers[k] = r.headers[k]

    logger.info("proxy(noauth) ← %s %s (status=%d content_length=%d)", request.method, url, r.status_code, len(r.content))
    
    # 格式化响应中的时间字段
    if "application/json" in r.headers.get("content-type", ""):
        try:
            data = r.json()
            data = _format_response_times(data)
            content = json.dumps(data, ensure_ascii=False).encode("utf-8")
            out_headers["content-type"] = "application/json; charset=utf-8"
            return Response(content=content, status_code=r.status_code, headers=out_headers)
        except Exception as e:
            logger.warning("failed to format response times (noauth): %s — returning original", e)
    
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)
