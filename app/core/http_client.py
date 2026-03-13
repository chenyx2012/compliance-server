from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import httpx


async def post_json(
    *,
    base_url: str,
    path: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[bool, Optional[Any], Optional[str], int]:
    """向下游发送 POST JSON 请求，并将错误转换为可序列化信息。"""
    start = time.perf_counter()
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return True, data, None, elapsed_ms
    except httpx.HTTPStatusError as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        msg = f"HTTP {e.response.status_code}: {e.response.text}"
        return False, None, msg, elapsed_ms
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return False, None, str(e), elapsed_ms

