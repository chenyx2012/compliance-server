from __future__ import annotations

from app.core.config import Settings


def test_effective_uploader_token_falls_back_to_access_key():
    s = Settings.model_construct(
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_bucket_name="compliance",
        s3_uploader_token=None,
        s3_app_token="",
        s3_uploader_bucket_name="",
        s3_uploader_region="",
        s3_region="us-east-1",
        s3_uploader_bucket_path="",
        s3_bucket_path="csv/",
    )
    assert s.effective_s3_uploader_token == "minioadmin"
    assert s.effective_s3_uploader_bucket_name == "compliance"
    assert s.effective_s3_uploader_bucket_path == "csv/"


def test_s3_configured_helper(monkeypatch):
    from app.services import s3_client

    monkeypatch.setattr(s3_client.settings, "s3_access_key", "ak")
    monkeypatch.setattr(s3_client.settings, "s3_secret_key", "sk")
    monkeypatch.setattr(s3_client.settings, "s3_uploader_token", None)
    monkeypatch.setattr(s3_client.settings, "s3_app_token", "")
    monkeypatch.setattr(s3_client.settings, "s3_uploader_bucket_name", "")
    monkeypatch.setattr(s3_client.settings, "s3_bucket_name", "compliance")
    assert s3_client.s3_configured() is True


def test_build_s2_payload():
    from app.tasks.s2_scan_tasks import build_s2_scan_payload

    p = build_s2_scan_payload(
        task_id="t1",
        download_path="compliance/csv/foo.zip/",
        upload_path="/compliance/csv/",
        scan_mode="pr",
    )
    assert p["task_id"] == "t1"
    assert p["download_path"] == "compliance/csv/foo.zip"
    assert p["upload_path"] == "/compliance/csv/"
    assert p["scan_mode"] == "pr"
    assert p["sync"] is False
    # 兼容旧字段仍保留
    assert p["s3_bucket"]
    assert "s3_key" in p
    assert p["topk"] == 1
