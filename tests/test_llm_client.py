from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import llm_client


@pytest.fixture
def fake_llm(monkeypatch):
    """模拟 vLLM 的 OpenAI 兼容接口，记录收到的请求。"""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        seen.append({"path": request.url.path, "auth": request.headers.get("authorization"), "body": body})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen-test"}]})
        if body.get("stream"):
            chunks = [
                {"choices": [{"index": 0, "delta": {"content": "你"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": "stop"}]},
            ]
            text = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(settings, "llm_base_url", "http://llm.local:8100/v1/")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_model", "qwen-test")
    monkeypatch.setattr(llm_client, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def test_chat_completions_payload_and_auth(fake_llm):
    resp = llm_client.chat_completions(messages=[{"role": "user", "content": "hi"}], max_tokens=5)
    assert resp["choices"][0]["message"]["content"] == "ok"
    req = fake_llm[0]
    assert req["path"] == "/v1/chat/completions"
    assert req["auth"] == "Bearer sk-test"
    assert req["body"]["model"] == "qwen-test"
    assert req["body"]["max_tokens"] == 5
    assert "top_p" not in req["body"]


def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "")
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        llm_client.chat_completions(messages=[{"role": "user", "content": "hi"}])


def test_ai_api_chat_and_models(fake_llm):
    client = TestClient(app)
    r = client.post("/api/v1/ai/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion" and body["model"] == "qwen-test"
    assert client.get("/api/v1/ai/models").json()["data"][0]["id"] == "qwen-test"
    assert client.get("/api/v1/ai/health").json()["status"] == "ok"


def test_ai_api_stream_passthrough(fake_llm):
    client = TestClient(app)
    r = client.post("/api/v1/ai/chat/completions/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    lines = [l for l in r.text.splitlines() if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    content = "".join(json.loads(l[6:])["choices"][0]["delta"]["content"] for l in lines[:-1])
    assert content == "你好"


def test_huawei_endpoints_removed():
    client = TestClient(app)
    assert client.post("/api/v1/ai/get-token").status_code == 404
    assert client.post("/api/v1/ai/call-huawei", json={"data": {}}).status_code == 404


def test_healthz_reports_llm(fake_llm):
    client = TestClient(app)
    ai = client.get("/healthz", params={"deep": "true"}).json()["checks"]["ai"]
    assert ai["configured"] is True and ai["ok"] is True and ai["model"] == "qwen-test"
