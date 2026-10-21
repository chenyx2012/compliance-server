"""通用大模型接口（/api/v1/ai），后端为开源大模型（OpenAI 兼容服务）。

调用模型的端点用普通 def：FastAPI 会放进线程池执行，避免阻塞事件循环。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.llm_client import (
    chat_completions,
    list_models,
    llm_config,
    ping_model,
    stream_chat_completions,
)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str = Field(default_factory=lambda: settings.llm_model)
    messages: List[ChatMessage]
    temperature: float = Field(default=0.1, ge=0, le=2)
    stream: bool = False
    max_tokens: Optional[int] = Field(default=None, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    top_k: Optional[int] = Field(default=None, ge=1)


def _call(req: ChatCompletionsRequest, timeout: float) -> Dict[str, Any]:
    return chat_completions(
        messages=[m.model_dump() for m in req.messages],
        model=req.model,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        top_p=req.top_p,
        top_k=req.top_k,
        timeout=timeout,
    )


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError):
        return HTTPException(
            status_code=e.response.status_code,
            detail={"error": {"message": e.response.text, "code": "HTTP_ERROR"}},
        )
    return HTTPException(status_code=500, detail={"error": {"message": str(e), "code": "INTERNAL_ERROR"}})


@router.post("/chat/completions")
def chat_completions_api(req: ChatCompletionsRequest) -> Dict[str, Any]:
    try:
        raw = _call(req, timeout=60.0)
    except Exception as e:
        raise _http_error(e)
    choice0 = (raw.get("choices") or [{}])[0]
    msg = choice0.get("message") or {"role": "assistant", "content": ""}
    return {
        "id": raw.get("id") or "chatcmpl-local",
        "object": "chat.completion",
        "created": raw.get("created") or int(datetime.now(timezone.utc).timestamp()),
        "model": raw.get("model") or req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": msg.get("role", "assistant"), "content": msg.get("content", "")},
                "finish_reason": choice0.get("finish_reason") or "stop",
            }
        ],
        "usage": raw.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/chat/completions/stream")
def chat_completions_stream(req: ChatCompletionsRequest) -> StreamingResponse:
    def gen() -> Iterator[str]:
        try:
            for line in stream_chat_completions(
                messages=[m.model_dump() for m in req.messages],
                model=req.model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                top_p=req.top_p,
                top_k=req.top_k,
            ):
                yield f"{line}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'code': 'INTERNAL_ERROR'}}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
def health() -> Dict[str, Any]:
    if not llm_config.is_configured:
        return {"status": "unhealthy: 大模型未配置（LLM_BASE_URL / LLM_MODEL）", "timestamp": None, "version": "1.0.0"}
    result = ping_model()
    if result.get("reachable"):
        return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat(), "version": "1.0.0"}
    return {"status": f"unhealthy: {result.get('error')}", "timestamp": None, "version": "1.0.0"}


@router.get("/models")
def models() -> Dict[str, Any]:
    try:
        return list_models()
    except Exception:
        return {
            "object": "list",
            "data": [{"id": settings.llm_model, "object": "model", "created": 0, "owned_by": "open-source"}],
        }


@router.get("/metrics")
def metrics() -> Dict[str, Any]:
    return {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "average_response_time": 0,
        "cache_hit_rate": 0,
    }


@router.post("/chat")
def chat(body: Dict[str, Any]) -> Dict[str, Any]:
    if "messages" not in body:
        raise HTTPException(status_code=400, detail="缺少消息数据")
    try:
        raw = chat_completions(
            messages=body["messages"],
            model=body.get("model") or settings.llm_model,
            temperature=body.get("temperature", 0.7),
            timeout=300.0,
        )
    except Exception as e:
        raise _http_error(e)
    return {"success": True, "model": raw.get("model") or settings.llm_model, "response": raw}
