from __future__ import annotations

import asyncio
import base64
import logging
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from app.core.config import settings


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


def normalize_git_clone_url(url: str) -> str:
    """
    规范化为可 git clone 的仓库地址，支持带 .git 和不带两种。
    不修改协议与路径，仅去除首尾空白；若含 /blob/ 等则视为非法仓库 URL。
    """
    u = url.strip()
    if not u:
        raise ValueError("git url is empty")
    lower = u.lower()
    # 明确是压缩包或单文件地址则不允许
    if any(lower.endswith(s) for s in (".zip", ".tar.gz", ".tgz", ".tar")):
        raise ValueError("url looks like an archive; use a git repo url (with or without .git)")
    if "/blob/" in lower or "/raw/" in lower or "/tree/" in lower:
        raise ValueError("url looks like a file/blob url; use repo root url, e.g. https://github.com/owner/repo or https://github.com/owner/repo.git")
    return u


async def _clone_repo(git_url: str, dest_dir: Path, *, timeout_seconds: int = 300) -> Path:
    """
    将 git 仓库 clone 到 dest_dir，返回仓库根目录（即 dest_dir 本身）。
    git_url 支持带 .git 和不带两种；使用浅克隆 --depth 1。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--single-branch", git_url, str(dest_dir)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("git clone timeout")
    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        raise ValueError(f"git clone failed: {err}")
    return dest_dir


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


async def _upload_extracted_folder_to_s3(local_folder_absolute_path: Path) -> Optional[str]:
    """
    解压完成后调用 s3_uploader.py 将本地目录上传到 S3。
    若未配置 S3_APP_TOKEN 或脚本不存在则跳过；失败时返回错误信息，成功返回 None。
    """
    if not (getattr(settings, "s3_app_token", None) and settings.s3_bucket_name):
        return None
    script_path = Path(settings.s3_uploader_script)
    if not script_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent
        script_path = project_root / settings.s3_uploader_script
    if not script_path.exists():
        logging.warning("S3 uploader script not found: %s, skip S3 upload", script_path)
        return None
    folder_str = str(local_folder_absolute_path.resolve())
    cmd = [
        sys.executable,
        str(script_path),
        f"--local_folder_absolute_path={folder_str}",
        f"--app_token={settings.s3_app_token}",
        f"--region={settings.s3_region}",
        f"--bucket_name={settings.s3_bucket_name}",
        f"--bucket_path={settings.s3_bucket_path}",
        "--show_speed",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(script_path.parent),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
            return err or f"exit code {proc.returncode}"
        return None
    except asyncio.TimeoutError:
        return "S3 upload timeout (600s)"
    except Exception as e:
        return str(e)


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


async def ingest_from_url(
    url: str,
    *,
    timeout_seconds: int = 300,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    通过 git clone 获取仓库代码并解析为目录树。
    url 支持带 .git 和不带两种，例如：
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://gitee.com/owner/repo.git
    """
    clone_url = normalize_git_clone_url(url)
    # 从 URL 末段提取仓库名，去掉 .git 后缀，作为本地目录名
    # 例：https://gitcode.com/openeuler/IB_Robot.git → IB_Robot
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git").strip() or "repo"

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        repo_root = td_path / repo_name
        await _clone_repo(clone_url, repo_root, timeout_seconds=timeout_seconds)
        s3_err = await _upload_extracted_folder_to_s3(repo_root)
        tree = build_dir_tree(repo_root, display_root=repo_root.name)
        meta = {
            "source": "url",
            "url": url,
            "type": "git",
            "clone_url": clone_url,
            "s3_upload": "Success" if s3_err is None else s3_err,
        }
        if s3_err:
            meta["s3_upload_error"] = s3_err
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
            s3_err = await _upload_extracted_folder_to_s3(root)
            tree = build_dir_tree(root, display_root=root.name)
            meta = {"source": "upload", "type": "archive", "filename": uploaded_filename, "s3_upload": "Success" if s3_err is None else s3_err}
            return tree, meta

        # 用文件名（保留扩展名）作为根目录名，例：main.py → main.py
        file_stem = uploaded_filename if uploaded_filename else "upload"
        single_root = td_path / (file_stem or "upload")
        single_root.mkdir(parents=True, exist_ok=True)
        dest = single_root / (uploaded_filename or "file")
        dest.write_bytes(data)
        s3_err = await _upload_extracted_folder_to_s3(single_root)
        tree = build_dir_tree(single_root, display_root=single_root.name)
        meta = {"source": "upload", "type": "file", "filename": uploaded_filename, "s3_upload": "Success" if s3_err is None else s3_err}
        return tree, meta

