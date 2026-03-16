from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.file_ingest import build_dir_tree


def _mk_zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _find_node_by_path(node: dict, path: str) -> dict | None:
    if node.get("path") == path:
        return node
    for child in (node.get("next") or {}).values():
        r = _find_node_by_path(child, path)
        if r is not None:
            return r
    return None


def test_build_dir_tree_text_leaf(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('hi')\n", encoding="utf-8")

    tree = build_dir_tree(tmp_path, display_root="root")
    print("\n[tree:test_build_dir_tree_text_leaf]\n" + json.dumps(tree, ensure_ascii=False, indent=2))

    assert tree["path"] == "root"
    leaf = _find_node_by_path(tree, "src/a.py")
    assert leaf is not None
    assert leaf["next"] == {}
    assert isinstance(leaf["content"], dict)
    assert leaf["content"]["text"].replace("\r\n", "\n") == "print('hi')\n"


def test_files_ingest_upload_zip(override_get_db):
    client = TestClient(app)
    zbytes = _mk_zip_bytes(
        {
            "repo/README.md": b"# hello\n",
            "repo/app/main.py": b"print('x')\n",
        }
    )

    resp = client.post("/files/ingest", files={"file": ("repo.zip", zbytes, "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "ingest_id" in body
    assert body["meta"]["source"] == "upload"
    assert body["meta"]["type"] == "archive"

    tree = body["tree"]
    print("\n[tree:test_files_ingest_upload_zip]\n" + json.dumps(tree, ensure_ascii=False, indent=2))
    readme = _find_node_by_path(tree, "README.md") or _find_node_by_path(tree, "repo/README.md")
    assert readme is not None
    assert readme["content"]["text"].startswith("# hello")


def test_files_ingest_url_mock_download(monkeypatch, override_get_db):
    """
    Avoid real network: monkeypatch app.file_ingest.download_to_temp to return a temp zip.
    """
    import tempfile
    from pathlib import Path

    import app.services.file_ingest as fi

    zbytes = _mk_zip_bytes({"repo/a.txt": b"abc\n"})

    async def fake_download_to_temp(url: str, *, timeout_seconds: float = 60.0):
        fd, p = tempfile.mkstemp(suffix=".zip")
        Path(p).write_bytes(zbytes)
        return Path(p), "fake.zip"

    monkeypatch.setattr(fi, "download_to_temp", fake_download_to_temp)

    client = TestClient(app)
    resp = client.post("/files/ingest", data={"source_url": "https://github.com/o/r/blob/main/a.zip"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "ingest_id" in body
    assert body["meta"]["source"] == "url"
    assert body["meta"]["type"] == "archive"

    tree = body["tree"]
    print("\n[tree:test_files_ingest_url_mock_download]\n" + json.dumps(tree, ensure_ascii=False, indent=2))
    a_txt = _find_node_by_path(tree, "a.txt") or _find_node_by_path(tree, "repo/a.txt")
    assert a_txt is not None
    assert a_txt["content"]["text"] == "abc\n"

