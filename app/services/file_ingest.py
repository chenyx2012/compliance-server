from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

CODE_TREE_EXTENSIONS = {".cpp", ".cc", ".h", ".hpp", ".c"}
MAX_CODE_FILE_BYTES = 1 * 1024 * 1024


def _kill_process_tree(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            import signal

            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as e:
        logger.debug("_kill_process_tree pid=%d error=%s", pid, e)


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
    u = url.strip()
    if not u:
        raise ValueError("git url is empty")
    lower = u.lower()
    if any(lower.endswith(s) for s in (".zip", ".tar.gz", ".tgz", ".tar")):
        raise ValueError("url looks like an archive; use a git repo url (with or without .git)")
    if "/blob/" in lower or "/raw/" in lower or "/tree/" in lower:
        raise ValueError(
            "url looks like a file/blob url; use repo root url, e.g. https://github.com/owner/repo"
        )
    return u


async def _clone_repo(git_url: str, dest_dir: Path, *, timeout_seconds: int = 300) -> Path:
    if dest_dir.exists():
        logger.warning("git clone — dest already exists, removing — dest=%s", dest_dir)
        shutil.rmtree(dest_dir, ignore_errors=True)

    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    logger.info("git clone start — url=%s dest=%s timeout=%ds", git_url, dest_dir, timeout_seconds)
    kwargs: Dict[str, Any] = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True

    cmd = ["git", "clone", "--depth", "1", "--single-branch", git_url, str(dest_dir)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.error("git clone timeout — url=%s timeout=%ds", git_url, timeout_seconds)
        _kill_process_tree(proc.pid)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ValueError("git clone timeout")
    except Exception:
        _kill_process_tree(proc.pid)
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise

    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
        logger.error("git clone failed — url=%s returncode=%d stderr=%s", git_url, proc.returncode, err[:500])
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ValueError(f"git clone failed: {err}")

    logger.info("git clone success — url=%s dest=%s", git_url, dest_dir)
    return dest_dir


def _is_zip(path: Path) -> bool:
    return zipfile.is_zipfile(path)


def _is_tar(path: Path) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except Exception:
        return False


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    logger.info("extract archive start — archive=%s dest=%s", archive_path.name, dest_dir)
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


def _resolve_uploader_script() -> Optional[Path]:
    script_path = Path(settings.s3_uploader_script)
    if not script_path.is_absolute():
        project_root = Path(__file__).resolve().parent.parent.parent
        script_path = project_root / settings.s3_uploader_script
    if not script_path.exists():
        return None
    return script_path


async def _upload_single_file_to_s3(local_file: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    单文件上传。优先进程内 boto3（MinIO）；失败再回退 s3_uploader.py 子进程。
    返回 (error_msg, s3_path)；未配置时 (None, None)。
    """
    from app.services.s3_client import s3_configured, subprocess_s3_env, upload_file

    if not s3_configured():
        logger.info("s3 upload skip — credentials/bucket not configured")
        return None, None

    # 进程内上传（推荐 MinIO）
    err, s3_path = await asyncio.to_thread(upload_file, local_file)
    if s3_path:
        return None, s3_path
    if err is None:
        return None, None

    # 回退 CLI 脚本（规格契约），注入 MinIO 环境变量
    token = settings.effective_s3_uploader_token
    bucket = settings.effective_s3_uploader_bucket_name
    script_path = _resolve_uploader_script()
    if script_path is None or not token:
        return err, None

    region = settings.effective_s3_uploader_region
    bucket_path = settings.effective_s3_uploader_bucket_path
    cmd = [
        sys.executable,
        str(script_path),
        f"--local_folder_absolute_path={str(local_file.resolve())}",
        f"--app_token={token}",
        f"--region={region}",
        f"--bucket_name={bucket}",
        f"--bucket_path={bucket_path}",
        "--show_speed",
    ]
    fallback_path = f"{bucket}/{bucket_path}{local_file.name}"
    logger.warning("s3 in-process upload failed (%s), fallback to script", err)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(script_path.parent),
            env=subprocess_s3_env(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            return (out or f"exit code {proc.returncode}"), None
        return None, fallback_path
    except asyncio.TimeoutError:
        return "S3 upload timeout (600s)", None
    except Exception as e:
        return str(e), None


async def _upload_extracted_folder_to_s3(local_folder: Path) -> Tuple[Optional[str], Optional[str]]:
    """文件夹上传。返回 (error_msg, s3_path)。"""
    from app.services.s3_client import s3_configured, upload_directory

    if not s3_configured():
        return None, None
    return await asyncio.to_thread(upload_directory, local_folder)


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


def _batch_insert_code_files(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    from app.core.sync_db import sync_session
    from app.models.code_file import CodeFile

    with sync_session() as session:
        for r in rows:
            session.add(CodeFile(**r))


def save_code_tree(
    root: Path,
    ingest_id: Optional[int],
    task_id: Optional[str],
    *,
    keep_root_prefix: bool = False,
) -> int:
    """
    遍历白名单扩展名写入 code_file。
    keep_root_prefix=True 时 file_path 保留 root.name 前缀（local ingest）。
    """
    root = root.resolve()
    batch: List[Dict[str, Any]] = []
    stored = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in CODE_TREE_EXTENSIONS:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size == 0 or size > MAX_CODE_FILE_BYTES:
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue

        rel = p.relative_to(root).as_posix()
        if keep_root_prefix:
            file_path = f"{root.name}/{rel}" if rel != "." else root.name
        else:
            file_path = rel

        batch.append(
            {
                "ingest_id": ingest_id,
                "task_id": task_id,
                "file_path": file_path,
                "content": content,
                "file_size": size,
                "extension": ext,
            }
        )
        if len(batch) >= 100:
            _batch_insert_code_files(batch)
            stored += len(batch)
            batch = []

    if batch:
        _batch_insert_code_files(batch)
        stored += len(batch)
    logger.info("save_code_tree done — root=%s stored=%d task_id=%s", root, stored, task_id)
    return stored


def _ensure_project_dir(project_name: str) -> Path:
    base = Path(settings.temp_storage_root) / "tmp" / project_name
    base.mkdir(parents=True, exist_ok=True)
    return base


def _git_diff_files(repo_root: Path, base_ref: str, head_ref: str) -> List[str]:
    """返回 base..head 变更文件相对路径（含新增/修改；删除忽略）。"""
    cmd = ["git", "-C", str(repo_root), "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...{head_ref}"]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning("git diff failed: %s", e)
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _pack_zip(
    repo_root: Path,
    zip_path: Path,
    *,
    only_files: Optional[List[str]] = None,
) -> int:
    """打包 zip；only_files 为相对 repo_root 的路径列表（PR 增量）。返回写入文件数。"""
    if zip_path.exists():
        zip_path.unlink()
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if only_files is not None:
            for rel in only_files:
                src = repo_root / rel
                if src.is_file():
                    zf.write(src, arcname=f"{repo_root.name}/{rel}")
                    count += 1
        else:
            for p in repo_root.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(repo_root.parent)))
                    count += 1
    return count


async def ingest_from_url(
    url: str,
    *,
    timeout_seconds: int = 300,
    base_ref: Optional[str] = None,
    head_ref: Optional[str] = None,
    incremental: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """
    git clone → 打包 zip → S3 单文件上传 → 返回 (meta, repo_root_path)。
    incremental=True 且提供 base_ref/head_ref 时只打包变更文件（PR 增量）。
    """
    logger.info(
        "ingest_from_url start — url=%s incremental=%s base=%s head=%s",
        url,
        incremental,
        base_ref,
        head_ref,
    )
    clone_url = normalize_git_clone_url(url)
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git").strip() or "repo"

    td_path = _ensure_project_dir(repo_name)
    repo_root = td_path / repo_name
    await _clone_repo(clone_url, repo_root, timeout_seconds=timeout_seconds)

    zip_path = td_path / f"{repo_name}.zip"
    only_files: Optional[List[str]] = None
    pack_mode = "full"
    if incremental and base_ref and head_ref:
        only_files = _git_diff_files(repo_root, base_ref, head_ref)
        if only_files:
            pack_mode = "incremental"
        else:
            logger.warning("incremental diff empty, fallback to full pack")
            only_files = None
            pack_mode = "full_fallback"

    n_files = _pack_zip(repo_root, zip_path, only_files=only_files)

    s3_err, s3_path = await _upload_single_file_to_s3(zip_path)
    meta: Dict[str, Any] = {
        "source": "url",
        "url": url,
        "type": "git",
        "clone_url": clone_url,
        "pack_mode": pack_mode,
        "pack_file_count": n_files,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "s3_upload": "Success" if s3_err is None and s3_path else ("Failed" if s3_err else "Skipped"),
        "s3_path": s3_path,
    }
    if s3_err:
        meta["s3_upload"] = "Failed"
        meta["s3_upload_error"] = s3_err
    logger.info("ingest_from_url done — url=%s s3_path=%s pack=%s files=%d", url, s3_path, pack_mode, n_files)
    return meta, str(repo_root)


async def ingest_from_upload(
    uploaded_filename: str,
    data: bytes,
) -> Tuple[Dict[str, Any], str]:
    """上传解压 → S3 → 返回 (meta, root_path)。"""
    logger.info("ingest_from_upload start — filename=%s size=%d", uploaded_filename, len(data))
    project_name = Path(uploaded_filename or "upload.bin").stem or "upload"
    td_path = _ensure_project_dir(project_name)
    tmp = td_path / (uploaded_filename or "upload.bin")
    tmp.write_bytes(data)

    if _is_zip(tmp) or _is_tar(tmp):
        extract_dir = td_path / "extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        _extract_archive(tmp, extract_dir)
        root = _pick_single_root(extract_dir)
        # 上传原始压缩包为单文件（与规格「上传 zip」一致）
        s3_err, s3_path = await _upload_single_file_to_s3(tmp)
        meta = {
            "source": "upload",
            "type": "archive",
            "filename": uploaded_filename,
            "s3_upload": "Success" if s3_err is None and s3_path else ("Failed" if s3_err else "Skipped"),
            "s3_path": s3_path,
        }
        if s3_err:
            meta["s3_upload"] = "Failed"
            meta["s3_upload_error"] = s3_err
        return meta, str(root)

    root = td_path
    s3_err, s3_path = await _upload_single_file_to_s3(tmp)
    meta = {
        "source": "upload",
        "type": "file",
        "filename": uploaded_filename,
        "s3_upload": "Success" if s3_err is None and s3_path else ("Failed" if s3_err else "Skipped"),
        "s3_path": s3_path,
    }
    if s3_err:
        meta["s3_upload"] = "Failed"
        meta["s3_upload_error"] = s3_err
    return meta, str(root)
