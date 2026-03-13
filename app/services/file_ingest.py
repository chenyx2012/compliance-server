from __future__ import annotations

import base64
import posixpath
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}


def _safe_join_url(base: str, *parts: str) -> str:
    p = "/".join([base.rstrip("/"), *[s.strip("/") for s in parts]])
    return p


def normalize_git_hosted_url(url: str) -> str:
    u = url.strip()
    lower = u.lower()

    if any(lower.endswith(s) for s in (".zip", ".tar.gz", ".tgz", ".tar")):
        return u

    if "github.com/" in lower and "/blob/" in lower:
        try:
            _, after = u.split("github.com/", 1)
            owner_repo, rest = after.split("/blob/", 1)
            ref, file_path = rest.split("/", 1)
            return _safe_join_url("https://raw.githubusercontent.com", owner_repo, ref, file_path)
        except ValueError:
            return u

    for host in ("gitee.com", "gitcode.com"):
        if host in lower and "/blob/" in lower:
            return u.replace("/blob/", "/raw/")

    return u


def _detect_filename_from_url(url: str) -> str:
    try:
        name = posixpath.basename(url.split("?", 1)[0].split("#", 1)[0])
        return name or "download"
    except Exception:
        return "download"


async def download_to_temp(url: str, *, timeout_seconds: float = 60.0) -> Tuple[Path, str]:
    filename = _detect_filename_from_url(url)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        tmp_path = Path(f.name)

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        r = await client.get(url)
        r.raise_for_status()
        tmp_path.write_bytes(r.content)

    return tmp_path, filename


def _is_zip(path: Path) -> bool:
    return zipfile.is_zipfile(path)


def _is_tar(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except Exception:
        return False


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    if _is_zip(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
        return
    if _is_tar(archive_path):
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(dest_dir)
        return
    raise ValueError("unsupported archive format")


def _pick_single_root(extract_dir: Path) -> Path:
    children = [p for p in extract_dir.iterdir() if p.name not in {".DS_Store"}]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_dir


def _read_text_best_effort(path: Path, *, max_bytes: int) -> Dict[str, Any]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        return {"truncated": True, "max_bytes": max_bytes, "size_bytes": len(data), "text": ""}

    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return {"truncated": False, "size_bytes": len(data), "text": data.decode(enc)}
        except UnicodeDecodeError:
            continue

    return {
        "binary": True,
        "size_bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


def _iter_paths(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
        yield p


def build_dir_tree(
    root_dir: Path,
    *,
    display_root: str = "",
    ignore_dirs: Optional[set[str]] = None,
    max_file_bytes: int = 512 * 1024,
) -> Dict[str, Any]:
    ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS
    root_dir = root_dir.resolve()

    def make_node(path_str: str) -> Dict[str, Any]:
        return {"path": path_str, "next": {}, "content": None}

    root_node = make_node(display_root or root_dir.name or ".")
    nodes: Dict[str, Dict[str, Any]] = {"": root_node}

    for p in _iter_paths(root_dir):
        rel = p.relative_to(root_dir)
        rel_parts = rel.parts
        if any(part in ignore_dirs for part in rel_parts):
            continue

        rel_posix = rel.as_posix()
        parent_rel = rel.parent.as_posix()
        if parent_rel == ".":
            parent_rel = ""

        parent = nodes.get(parent_rel)
        if parent is None:
            cur = ""
            for part in rel_parts[:-1]:
                nxt = part if not cur else f"{cur}/{part}"
                if nxt not in nodes:
                    nodes[nxt] = make_node(nxt)
                    nodes[cur]["next"][part] = nodes[nxt]
                cur = nxt
            parent = nodes[parent_rel]

        name = rel_parts[-1]
        if rel_posix not in nodes:
            nodes[rel_posix] = make_node(rel_posix)
            parent["next"][name] = nodes[rel_posix]

        if p.is_file():
            nodes[rel_posix]["content"] = _read_text_best_effort(p, max_bytes=max_file_bytes)
            nodes[rel_posix]["next"] = {}

    return root_node


async def ingest_from_url(url: str, *, timeout_seconds: float = 60.0) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    normalized = normalize_git_hosted_url(url)
    tmp_file, filename = await download_to_temp(normalized, timeout_seconds=timeout_seconds)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        if _is_zip(tmp_file) or _is_tar(tmp_file):
            _extract_archive(tmp_file, td_path)
            root = _pick_single_root(td_path)
            tree = build_dir_tree(root, display_root=root.name)
            meta = {"source": "url", "url": url, "normalized_url": normalized, "type": "archive", "filename": filename}
            return tree, meta

        single_root = td_path / "upload"
        single_root.mkdir(parents=True, exist_ok=True)
        dest = single_root / (filename or tmp_file.name)
        dest.write_bytes(tmp_file.read_bytes())
        tree = build_dir_tree(single_root, display_root=single_root.name)
        meta = {"source": "url", "url": url, "normalized_url": normalized, "type": "file", "filename": filename}
        return tree, meta


async def ingest_from_upload(
    uploaded_filename: str,
    data: bytes,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        tmp = td_path / (uploaded_filename or "upload.bin")
        tmp.write_bytes(data)

        if _is_zip(tmp) or _is_tar(tmp):
            extract_dir = td_path / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            _extract_archive(tmp, extract_dir)
            root = _pick_single_root(extract_dir)
            tree = build_dir_tree(root, display_root=root.name)
            meta = {"source": "upload", "type": "archive", "filename": uploaded_filename}
            return tree, meta

        single_root = td_path / "upload"
        single_root.mkdir(parents=True, exist_ok=True)
        dest = single_root / (uploaded_filename or "file")
        dest.write_bytes(data)
        tree = build_dir_tree(single_root, display_root=single_root.name)
        meta = {"source": "upload", "type": "file", "filename": uploaded_filename}
        return tree, meta

