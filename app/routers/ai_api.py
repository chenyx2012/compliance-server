"""华为 AI / OpenAI 兼容 chat 接口（/api/v1/ai）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.huawei_ai_config import chat_completions, huawei_ai_config

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str = Field(default_factory=lambda: settings.ai_model_default)
    messages: List[ChatMessage]
    temperature: float = Field(default=0.1, ge=0, le=2)
    stream: bool = False
    max_tokens: Optional[int] = Field(default=None, ge=1)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    top_k: Optional[int] = Field(default=None, ge=1)


@router.post("/chat/completions")
async def chat_completions_api(req: ChatCompletionsRequest) -> Dict[str, Any]:
    try:
        raw = chat_completions(
            messages=[m.model_dump() for m in req.messages],
            model=req.model,
            temperature=req.temperature,
            stream=False,
            max_tokens=req.max_tokens,
            top_p=req.top_p,
            top_k=req.top_k,
            timeout=30.0,
        )
        # 重新装配为统一 schema
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
            "usage": raw.get("usage")
            or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail={"error": {"message": e.response.text, "code": "HTTP_ERROR"}},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"message": str(e), "code": "INTERNAL_ERROR"}},
        )


@router.post("/chat/completions/stream")
async def chat_completions_stream(req: ChatCompletionsRequest):
    # 简化：非流式取结果后按 SSE 一片返回（兼容前端）
    async def gen():
        try:
            raw = chat_completions(
                messages=[m.model_dump() for m in req.messages],
                model=req.model,
                temperature=req.temperature,
                stream=False,
                max_tokens=req.max_tokens,
                top_p=req.top_p,
                top_k=req.top_k,
                timeout=60.0,
            )
            content = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            import json

            chunk = {
                "id": raw.get("id") or "chatcmpl-local",
                "object": "chat.completion.chunk",
                "created": int(datetime.now(timezone.utc).timestamp()),
                "model": req.model,
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            import json

            yield f"data: {json.dumps({'error': {'message': str(e), 'code': 'INTERNAL_ERROR'}})}\n\n"

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
async def health() -> Dict[str, Any]:
    try:
        if huawei_ai_config.use_openai_compat:
            return {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "1.0.0",
            }
        token = huawei_ai_config.get_dynamic_token()
        if token:
            return {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": "1.0.0",
            }
        return {"status": "unhealthy: 无法获取动态token", "timestamp": None, "version": "1.0.0"}
    except Exception as e:
        return {"status": f"unhealthy: {e}", "timestamp": None, "version": "1.0.0"}


@router.get("/models")
async def models() -> Dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": settings.ai_model_default,
                "object": "model",
                "created": 1677610602,
                "owned_by": "huawei",
            }
        ],
    }


@router.get("/metrics")
async def metrics() -> Dict[str, Any]:
    return {
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "average_response_time": 0,
        "cache_hit_rate": 0,
    }


@router.post("/get-token")
async def get_token() -> Dict[str, Any]:
    token = huawei_ai_config.get_dynamic_token()
    if token:
        return {"success": True, "token": token}
    return {"success": False, "error": "无法获取动态token"}


@router.post("/call-huawei")
async def call_huawei(body: Dict[str, Any]) -> Dict[str, Any]:
    if "data" not in body:
        raise HTTPException(status_code=400, detail="缺少请求数据")
    token = body.get("token") or huawei_ai_config.get_dynamic_token()
    if not token:
        raise HTTPException(status_code=400, detail="无法获取动态token")
    data = body["data"]
    max_retries = 2
    last_status = 500
    last_text = ""
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(verify=False, timeout=300.0) as client:
                if huawei_ai_config.use_openai_compat:
                    url = f"{huawei_ai_config.openai_base}/chat/completions"
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    }
                else:
                    url = huawei_ai_config.chat_url
                    headers = {"Content-Type": "application/json", "Authorization": token}
                r = client.post(url, headers=headers, json=data)
                last_status = r.status_code
                last_text = r.text
                if r.is_success:
                    return r.json()
                if (
                    r.status_code == 401
                    and "token has expired" in r.text
                    and attempt < max_retries
                ):
                    token = huawei_ai_config.get_dynamic_token(force_refresh=True)
                    continue
                raise HTTPException(status_code=r.status_code, detail=r.text)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=last_status, detail=last_text)


@router.post("/chat")
async def chat(body: Dict[str, Any]) -> Dict[str, Any]:
    if "messages" not in body:
        raise HTTPException(status_code=400, detail="缺少消息数据")
    # 规格 §16 #4：硬编码自环
    base = "http://localhost:8001/api/v1/ai"
    with httpx.Client(timeout=300.0) as client:
        tr = client.post(f"{base}/get-token")
        tr.raise_for_status()
        token_body = tr.json()
        if not token_body.get("success"):
            raise HTTPException(status_code=500, detail="无法获取动态token")
        token = token_body["token"]
        data = {
            "model": body.get("model") or settings.ai_model_default,
            "messages": body["messages"],
            "temperature": body.get("temperature", 0.7),
            "stream": body.get("stream", False),
        }
        cr = client.post(f"{base}/call-huawei", json={"token": token, "data": data}, timeout=300.0)
        cr.raise_for_status()
        return {
            "success": True,
            "token_used": token[:20] + "...",
            "response": cr.json(),
        }
