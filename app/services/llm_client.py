"""开源大模型客户端（OpenAI 兼容协议：vLLM / vLLM Ascend / Ollama 等）。

配置来自 Settings：LLM_BASE_URL（含 /v1）、LLM_API_KEY、LLM_MODEL。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterator, List, Optional

import httpx

from app.core.config import settings

# httpx.Client 线程安全；批量复核在线程池中并发调用，复用连接池
_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def http_client() -> httpx.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
                    trust_env=False,
                )
    return _client


class LLMConfig:
    @property
    def base_url(self) -> str:
        return (settings.llm_base_url or "").rstrip("/")

    @property
    def model(self) -> str:
        return settings.llm_model

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        return headers

    def describe(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured,
            "endpoint": self.base_url,
            "model": self.model,
            "qwen_configured": bool(settings.qwen_fp_base_url),
        }


llm_config = LLMConfig()


def _payload(
    messages: List[Dict[str, Any]],
    model: Optional[str],
    temperature: float,
    stream: bool,
    max_tokens: Optional[int],
    top_p: Optional[float],
    top_k: Optional[int],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model or llm_config.model,
        "messages": messages,
        "temperature": temperature,
        "stream": stream,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "top_k": top_k,  # vLLM 支持；其它实现会忽略
    }
    return {k: v for k, v in payload.items() if v is not None}


def _require_configured() -> None:
    if not llm_config.is_configured:
        raise RuntimeError("大模型未配置（LLM_BASE_URL / LLM_MODEL）")


def chat_completions(
    *,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    _require_configured()
    r = http_client().post(
        f"{llm_config.base_url}/chat/completions",
        headers=llm_config.headers(),
        json=_payload(messages, model, temperature, False, max_tokens, top_p, top_k),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def stream_chat_completions(
    *,
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    top_k: Optional[int] = None,
    timeout: float = 120.0,
) -> Iterator[str]:
    """透传模型的 SSE 行（每行形如 `data: {...}`，以 `data: [DONE]` 结束）。"""
    _require_configured()
    with http_client().stream(
        "POST",
        f"{llm_config.base_url}/chat/completions",
        headers=llm_config.headers(),
        json=_payload(messages, model, temperature, True, max_tokens, top_p, top_k),
        timeout=timeout,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                yield line


def list_models(timeout: float = 10.0) -> Dict[str, Any]:
    _require_configured()
    r = http_client().get(f"{llm_config.base_url}/models", headers=llm_config.headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def ping_model(timeout: float = 15.0) -> Dict[str, Any]:
    """发一次极小请求验证模型可达（供 /healthz?deep=true）。"""
    t0 = time.monotonic()
    try:
        chat_completions(messages=[{"role": "user", "content": "ping"}], max_tokens=1, timeout=timeout)
        return {"reachable": True, "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as e:
        return {"reachable": False, "error": str(e)[:300]}
