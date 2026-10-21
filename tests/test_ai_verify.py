from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.core.config import settings
from app.services import false_positive as fp
from app.services.ai_cache import ai_cache_service
from app.services.ai_verify import RAG_REASON_PREFIX, VerifyItem, judge_items


@pytest.fixture
def cache(monkeypatch) -> Dict[str, Any]:
    store: Dict[str, Any] = {}

    def get(md5, stage=None):
        return store.get(ai_cache_service.key(md5, stage))

    def get_many(md5_list):
        return {m: store[ai_cache_service.key(m)] for m in md5_list if ai_cache_service.key(m) in store}

    def set_(md5, value, stage=None, ttl=None):
        store[ai_cache_service.key(md5, stage)] = value

    monkeypatch.setattr(ai_cache_service, "get", get)
    monkeypatch.setattr(ai_cache_service, "get_many", get_many)
    monkeypatch.setattr(ai_cache_service, "set", set_)
    return store


def _reply(judgment: str, further: bool = False, reason: str = "r") -> Dict[str, Any]:
    content = json.dumps({"judgment": judgment, "reason": reason, "further_analysis": further}, ensure_ascii=False)
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def model(monkeypatch) -> List[Dict[str, Any]]:
    """可编程的假模型：calls 记录每次调用，replies 按顺序返回（异常实例会被抛出）。"""
    calls: List[Dict[str, Any]] = []
    replies: List[Any] = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        reply = replies.pop(0) if replies else _reply(fp.JUDGMENT_NO_RISK)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(fp, "chat_completions", fake_chat)
    monkeypatch.setattr(settings, "qwen_fp_base_url", "")
    return {"calls": calls, "replies": replies}


def test_model_failure_routes_to_manual_and_is_not_cached(cache, model):
    model["replies"].append(RuntimeError("connection refused"))
    r = fp.integrated_analysis(original_snippet="int a;", open_source_snippet="int a;", similarity=0.81)
    assert r["judgment"] == fp.JUDGMENT_NEED_CONFIRM
    assert r["ai_error"] is True
    assert cache == {}


def test_model_success_is_cached_per_step(cache, model):
    model["replies"].append(_reply(fp.JUDGMENT_NO_RISK, further=False))
    r = fp.integrated_analysis(original_snippet="x", open_source_snippet="y", similarity=0.9)
    assert r["judgment"] == fp.JUDGMENT_NO_RISK and r["ai_error"] is False
    h = fp.case_hash("x", "y")
    assert ai_cache_service.key(h, "ai_analyze") in cache


def test_further_analysis_runs_comparison(cache, model):
    model["replies"].extend([_reply(fp.JUDGMENT_NEED_CONFIRM, further=True), _reply(fp.JUDGMENT_NO_RISK)])
    r = fp.integrated_analysis(original_snippet="x", open_source_snippet="y")
    assert len(model["calls"]) == 2
    assert r["judgment"] == fp.JUDGMENT_NO_RISK


def test_case_hash_ignores_whitespace():
    assert fp.case_hash("int  a;\n\tint b;", "x") == fp.case_hash("int a; int b;", "x")
    assert fp.case_hash("a", "b") != fp.case_hash("a", "c")


def test_missing_snippet_skips_model(cache, model):
    out = judge_items([VerifyItem(key=1, original="int a;", open_source="", missing="开源片段缺失")])
    assert model["calls"] == []
    assert out.verdicts[1].judgment == fp.JUDGMENT_NEED_CONFIRM
    assert out.verdicts[1].source == "missing_snippet"
    assert out.stats["missing_snippet"] == 1


def test_duplicate_pairs_call_model_once_and_record_case(cache, model):
    items = [
        VerifyItem(key=1, original="int a;", open_source="int b;"),
        VerifyItem(key=2, original="int  a;", open_source="int b;"),
    ]
    out = judge_items(items, concurrency=4)
    assert len(model["calls"]) == 1
    assert {v.judgment for v in out.verdicts.values()} == {fp.JUDGMENT_NO_RISK}
    assert len(out.new_cases) == 1
    assert cache[ai_cache_service.key(fp.case_hash("int a;", "int b;"))]["judgment"] == fp.JUDGMENT_NO_RISK


def test_redis_hit_skips_model(cache, model):
    h = fp.case_hash("a", "b")
    cache[ai_cache_service.key(h)] = {"judgment": fp.JUDGMENT_NEED_CONFIRM, "reason": "cached"}
    out = judge_items([VerifyItem(key=1, original="a", open_source="b")])
    assert model["calls"] == []
    assert out.verdicts[1].source == "redis"
    assert out.verdicts[1].reason == "cached"


def test_rag_hit_marks_no_risk_without_model(cache, model):
    h = fp.case_hash("a", "b")
    out = judge_items(
        [VerifyItem(key=1, original="a", open_source="b")],
        rag_lookup=lambda hashes: {h: "已确认误报"} if h in hashes else {},
    )
    assert model["calls"] == []
    assert out.verdicts[1].judgment == fp.JUDGMENT_NO_RISK
    assert out.verdicts[1].reason == RAG_REASON_PREFIX + "已确认误报"
    assert out.new_cases == []


def test_model_error_is_not_cached_or_recorded(cache, model):
    model["replies"].append(RuntimeError("timeout"))
    out = judge_items([VerifyItem(key=1, original="a", open_source="b", similarity=0.81)])
    assert out.verdicts[1].judgment == fp.JUDGMENT_NEED_CONFIRM
    assert out.verdicts[1].source == "ai_error"
    assert out.stats["ai_errors"] == 1
    assert out.new_cases == []
    assert cache == {}


def _qwen(judgment: str, error: bool = False):
    def fake(original, open_source="", **_):
        fake.args = (original, open_source)
        return {"judgment": judgment, "reason": "qwen", "source": "selfdeveloped_ai", "ai_error": error}

    return fake


def test_dual_both_no_risk_skips_comparison(cache, model, monkeypatch):
    qwen = _qwen(fp.JUDGMENT_NO_RISK)
    monkeypatch.setattr(fp, "qwen_predict", qwen)
    model["replies"].append(_reply(fp.JUDGMENT_NO_RISK, further=False))
    r = fp.integrated_analysis(original_snippet="a", open_source_snippet="b", ai_type="dual")
    assert r["judgment"] == fp.JUDGMENT_NO_RISK
    assert len(model["calls"]) == 1
    assert qwen.args == ("a", "b")


def test_dual_disagreement_runs_comparison(cache, model, monkeypatch):
    monkeypatch.setattr(fp, "qwen_predict", _qwen(fp.JUDGMENT_NEED_CONFIRM))
    model["replies"].extend([_reply(fp.JUDGMENT_NO_RISK, further=False), _reply(fp.JUDGMENT_NEED_CONFIRM)])
    r = fp.integrated_analysis(original_snippet="a", open_source_snippet="b", ai_type="dual")
    assert len(model["calls"]) == 2
    assert r["judgment"] == fp.JUDGMENT_NEED_CONFIRM
    assert r["ai_error"] is False


def test_dual_all_unavailable_is_ai_error(cache, model, monkeypatch):
    monkeypatch.setattr(fp, "qwen_predict", _qwen(fp.JUDGMENT_NEED_CONFIRM, error=True))
    model["replies"].append(RuntimeError("down"))
    r = fp.integrated_analysis(original_snippet="a", open_source_snippet="b", ai_type="dual")
    assert r["judgment"] == fp.JUDGMENT_NEED_CONFIRM
    assert r["ai_error"] is True


def test_auto_mode_uses_dual_only_when_qwen_configured(monkeypatch):
    monkeypatch.setattr(settings, "qwen_fp_base_url", "")
    assert fp.resolve_ai_type("auto") == "llm"
    monkeypatch.setattr(settings, "qwen_fp_base_url", "http://qwen:8100")
    assert fp.resolve_ai_type("auto") == "dual"
    assert fp.resolve_ai_type("llm") == "llm"


def test_legacy_huawei_ai_type_maps_to_llm(cache, model):
    assert fp.resolve_ai_type("huawei_ai") == "llm"
    r = fp.integrated_analysis(original_snippet="a", open_source_snippet="b", ai_type="huawei_ai")
    assert r["ai_type"] == "llm" and len(model["calls"]) == 1


def test_llm_client_not_configured_routes_to_manual(cache, monkeypatch):
    from app.services import llm_client

    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(fp, "chat_completions", llm_client.chat_completions)
    monkeypatch.setattr(settings, "qwen_fp_base_url", "")
    r = fp.integrated_analysis(original_snippet="a", open_source_snippet="b")
    assert r["judgment"] == fp.JUDGMENT_NEED_CONFIRM and r["ai_error"] is True
    assert "LLM_BASE_URL" in r["reason"]
    assert cache == {}


class _FakeChain:
    captured: List[Any] = []

    def __init__(self, *steps):
        _FakeChain.captured = list(steps)

    def apply_async(self):
        return None


def _step_names(steps):
    names = []
    for s in steps:
        if hasattr(s, "tasks"):
            names.append(("group", [t.task for t in s.tasks]))
        else:
            names.append((s.task, s.options.get("queue")))
    return names


def test_version_postprocess_runs_ai_after_imports(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "chain", _FakeChain)
    open_info, self_info = main._enqueue_postprocess("/tmp/r.csv", "t1", "version")
    names = _step_names(_FakeChain.captured)
    assert names[0] == ("platform.parse_csv_and_save", settings.celery_queue_parse)
    assert names[1][0] == "group" and len(names[1][1]) == 5
    assert names[2] == ("platform.batch_ai_verify_task", settings.celery_queue_ai_verify)
    assert set(open_info) == {"nvidia", "cpp", "java"} and self_info["task_id"]


def test_pr_postprocess_skips_imports_by_default(monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "chain", _FakeChain)
    monkeypatch.setattr(settings, "pr_postprocess_imports", False)
    monkeypatch.setattr(settings, "pr_postprocess_ai_verify", True)
    open_info, self_info = main._enqueue_postprocess("/tmp/r.csv", "t1", "pr")
    names = _step_names(_FakeChain.captured)
    assert names == [
        ("platform.parse_csv_and_save", settings.celery_queue_parse),
        ("platform.batch_ai_verify_task", settings.celery_queue_ai_verify),
    ]
    assert open_info == {} and self_info is None
