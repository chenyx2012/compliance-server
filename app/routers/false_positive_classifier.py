"""误报分类路由（/api/v1/classifier）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.false_positive import (
    ai_analyze,
    comparison_analyze,
    integrated_analysis,
    qwen_predict,
)
from app.tasks.batch_ai_verify_tasks import batch_ai_verify_task

router = APIRouter(prefix="/api/v1/classifier", tags=["classifier"])


class SnippetPair(BaseModel):
    original_snippet: str = ""
    open_source_snippet: str = ""
    similarity: Optional[float] = None
    code_type: Optional[str] = None
    task_id: Optional[str] = None


class IntegratedBody(SnippetPair):
    ai_type: str = Field(default="huawei_ai")


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "false-positive-classifier"}


@router.post("/analyze")
async def analyze(body: SnippetPair) -> Dict[str, Any]:
    r = ai_analyze(
        original_snippet=body.original_snippet,
        open_source_snippet=body.open_source_snippet,
        similarity=body.similarity,
    )
    return {"success": True, **r}


@router.post("/decide")
async def decide(body: SnippetPair) -> Dict[str, Any]:
    """规则决策：相似度阈值 + 片段空值。"""
    if not (body.original_snippet or "").strip() or not (body.open_source_snippet or "").strip():
        return {
            "success": True,
            "judgment": "需要人工确认",
            "reason": "片段为空",
        }
    sim = float(body.similarity or 0)
    if sim < 0.7:
        return {"success": True, "judgment": "无风险", "reason": f"相似度偏低 ({sim})"}
    if sim >= 0.95:
        return {"success": True, "judgment": "需要人工确认", "reason": f"极高相似 ({sim})"}
    return {"success": True, "judgment": "需要人工确认", "reason": f"中高相似待复核 ({sim})"}


@router.post("/ai-analyze")
async def api_ai_analyze(body: SnippetPair) -> Dict[str, Any]:
    return {"success": True, **ai_analyze(
        original_snippet=body.original_snippet,
        open_source_snippet=body.open_source_snippet,
        similarity=body.similarity,
    )}


@router.post("/comparison-analyze")
async def api_comparison(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        **comparison_analyze(
            original_snippet=body.get("original_snippet") or "",
            open_source_snippet=body.get("open_source_snippet") or "",
            first_reason=body.get("first_reason") or body.get("reason") or "",
            similarity=body.get("similarity"),
        ),
    }


@router.post("/integrated-analysis")
async def api_integrated(body: IntegratedBody) -> Dict[str, Any]:
    return {
        "success": True,
        **integrated_analysis(
            original_snippet=body.original_snippet,
            open_source_snippet=body.open_source_snippet,
            similarity=body.similarity,
            ai_type=body.ai_type,
        ),
    }


@router.post("/batch-integrated-analysis")
async def batch_integrated(body: Dict[str, Any]) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = body.get("cases") or body.get("items") or []
    ai_type = body.get("ai_type") or "huawei_ai"
    results = []
    for i, c in enumerate(cases):
        r = integrated_analysis(
            original_snippet=c.get("original_snippet") or c.get("code_snippet") or "",
            open_source_snippet=c.get("open_source_snippet") or "",
            similarity=c.get("similarity"),
            ai_type=ai_type,
        )
        results.append({"index": i, **r})
    return {"success": True, "total": len(results), "results": results}


@router.post("/batch-ai-verify")
async def batch_ai_verify(body: Dict[str, Any]) -> Dict[str, Any]:
    task_id = body.get("task_id")
    if not task_id:
        return {"ok": False, "error": "task_id 是必填项"}
    ar = batch_ai_verify_task.apply_async(
        kwargs={
            "task_id": task_id,
            "use_rag": body.get("use_rag", True),
            "use_redis": body.get("use_redis", True),
            "force": body.get("force", False),
        }
    )
    return {"ok": True, "celery_task_id": ar.id, "task_id": task_id}


@router.post("/analyze-second-confirm-stats")
async def second_confirm_stats(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "stats": {"total": 0, "confirmed": 0, "rejected": 0}, "message": "ok"}


@router.post("/filter-test-cases")
async def filter_test_cases(body: Dict[str, Any]) -> Dict[str, Any]:
    cases = body.get("cases") or []
    filtered = [c for c in cases if (c.get("original_snippet") or c.get("open_source_snippet"))]
    return {"success": True, "total": len(cases), "filtered": len(filtered), "cases": filtered}


@router.post("/csv-to-json")
async def csv_to_json(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "message": "use /platform/code-tree/v1/ingest/csv for DB import", "data": []}


@router.post("/generate-json")
async def generate_json(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "cases": []}


@router.post("/validate-json-snippets")
async def validate_json_snippets(body: Dict[str, Any]) -> Dict[str, Any]:
    import json
    from pathlib import Path

    path = body.get("json_file_path")
    if not path or not Path(path).exists():
        return {
            "success": False,
            "json_file": path,
            "total_cases": 0,
            "empty_snippet_cases": 0,
            "valid_cases": 0,
            "error": "json_file_path not found",
        }
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data if isinstance(data, list) else data.get("cases") or []
    empty = 0
    valid = 0
    for c in cases:
        o = (c.get("original_snippet") or "").strip()
        s = (c.get("open_source_snippet") or "").strip()
        if not o or not s:
            empty += 1
        else:
            valid += 1
    return {
        "success": True,
        "json_file": path,
        "total_cases": len(cases),
        "empty_snippet_cases": empty,
        "valid_cases": valid,
    }
