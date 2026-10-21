from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_classifier_decide_empty():
    client = TestClient(app)
    r = client.post("/api/v1/classifier/decide", json={"original_snippet": "", "open_source_snippet": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["judgment"] == "需要人工确认"


def test_classifier_decide_low_sim():
    client = TestClient(app)
    r = client.post(
        "/api/v1/classifier/decide",
        json={
            "original_snippet": "int a=1;",
            "open_source_snippet": "int b=2;",
            "similarity": 0.3,
        },
    )
    assert r.status_code == 200
    assert r.json()["judgment"] == "无风险"


def test_ai_cache_stats():
    client = TestClient(app)
    r = client.get("/api/v1/ai-cache/stats")
    assert r.status_code == 200
    assert r.json()["success"] is True
