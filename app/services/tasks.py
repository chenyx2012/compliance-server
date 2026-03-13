from __future__ import annotations

"""
异步扫描编排与 Celery 任务定义。

下游契约（四类服务 A/B/C/D 通用，各服务只需满足以下约定即可接入）：
- 请求：网关对每类服务发送 POST {SERVICE_X_BASE_URL}{SERVICE_X_SCAN_PATH}
- 请求体：JSON {"target": "<扫描目标>", "options": {<前端透传的可选参数>}}
- 请求头：同步调用时可选透传 Authorization（由前端传入）
- 响应：任意 JSON，网关原样放入聚合结果的 data 字段；非 2xx 视为失败并记录 error
"""

import asyncio
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.http_client import post_json


# 模块代号 -> (BaseURL, ScanPath)；四类服务互不耦合，仅通过配置区分
MODULES = {
    "a": (settings.service_a_base_url, settings.service_a_scan_path),
    "b": (settings.service_b_base_url, settings.service_b_scan_path),
    "c": (settings.service_c_base_url, settings.service_c_scan_path),
    "d": (settings.service_d_base_url, settings.service_d_scan_path),
}


def _timeout_for_module(module_key: str) -> float:
    """每类服务可单独配置超时，未配置时使用全局 upstream_timeout_seconds。"""
    t = getattr(settings, f"service_{module_key}_timeout_seconds", None)
    return t if t is not None else settings.upstream_timeout_seconds


async def _run_scan_async(
    target: str,
    options: Dict[str, Any],
    modules: Optional[List[str]],
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    并发调用下游模块并聚合结果。调用方式对 A/B/C/D 一致，仅 URL 与 path 由配置决定。
    """
    selected = modules or ["a", "b", "c", "d"]
    selected = [m.lower() for m in selected]
    payload = {"target": target, "options": options}

    async def call_module(m: str) -> Dict[str, Any]:
        base_url, scan_path = MODULES[m]
        ok, data, error, elapsed_ms = await post_json(
            base_url=base_url,
            path=scan_path,
            payload=payload,
            timeout_seconds=_timeout_for_module(m),
            headers=headers,
        )
        return {
            "module": m,
            "ok": ok,
            "data": data,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }

    tasks = [call_module(m) for m in selected]
    results = await asyncio.gather(*tasks)
    return {"results": results}


@celery_app.task(name="compliance.scan")
def scan_task(target: str, options: Dict[str, Any], modules: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Celery 任务入口（运行在 worker 进程中）。
    """
    # Celery 异步任务不具备 HTTP 请求上下文，这里不透传 headers
    data = asyncio.run(_run_scan_async(target=target, options=options, modules=modules, headers=None))
    request_id = getattr(scan_task.request, "id", None)
    return {"request_id": request_id, "target": target, **data}

