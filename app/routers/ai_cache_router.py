from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.services.ai_cache import ai_cache_service

router = APIRouter(prefix="/api/v1/ai-cache", tags=["ai-cache"])


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    return {"success": True, "stats": ai_cache_service.stats()}


@router.post("/clear")
async def clear() -> Dict[str, Any]:
    n = ai_cache_service.clear()
    return {"success": True, "deleted_count": n, "message": f"已清空 {n} 条AI缓存"}
