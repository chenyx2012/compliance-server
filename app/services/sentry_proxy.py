"""
将请求原样转发到 compliance-sentry-main 的 /api/v1/*（与 analysis.py 中路由一致）。
"""

from __future__ import annotations

from typing import Any, Dict

import httpx
from fastapi import Request, Response


def _forward_headers(request: Request) -> Dict[str, str]:
    skip = {"host", "content-length", "connection", "transfer-encoding"}
    return {k: v for k, v in request.headers.items() if k.lower() not in skip}


async def proxy_to_sentry(base_url: str, path: str, request: Request) -> Response:
    """
    path 为 api/v1 之后的路径，如 analysis/tasks、mission/upload。
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/v1/{path}"
    query = str(request.url.query)
    if query:
        url = f"{url}?{query}"
    body = await request.body()
    headers = _forward_headers(request)
    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.request(
            request.method,
            url,
            content=body if body else None,
            headers=headers,
        )
    out_headers: Dict[str, str] = {}
    for k in ("content-type", "content-disposition"):
        if k in r.headers:
            out_headers[k] = r.headers[k]
    return Response(content=r.content, status_code=r.status_code, headers=out_headers)
