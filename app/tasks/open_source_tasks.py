from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.time_utils import beijing_now

logger = logging.getLogger(__name__)


def _sync_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = settings.database_url.replace("+aiomysql", "+pymysql")
    eng = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    return eng, sessionmaker(bind=eng)()


def _save_file_row(session, repo_name: str, file_path: str, content: str, s3_path: Optional[str] = None) -> None:
    from app.models.open_source_code_file import OpenSourceCodeFile

    ext = Path(file_path).suffix.lower()
    session.add(
        OpenSourceCodeFile(
            repo_name=repo_name,
            file_path=file_path.replace("\\", "/"),
            content=content,
            file_size=len(content.encode("utf-8", errors="ignore")),
            extension=ext or None,
            s3_path=s3_path,
            remote_url=None,
            created_at=beijing_now(),
        )
    )


def _import_path2_from_task(task_id: str, local_base_path: str, repo_name: str) -> Dict[str, Any]:
    from app.models.scan_result_code_clone import ScanResultCodeClone

    eng, session = _sync_session()
    stored = 0
    skipped = 0
    try:
        rows: List[ScanResultCodeClone] = (
            session.query(ScanResultCodeClone).filter(ScanResultCodeClone.task_id == task_id).all()
        )
        seen = set()
        base = Path(local_base_path)
        for r in rows:
            p2 = (r.path2 or "").replace("\\", "/")
            if not p2 or p2 in seen:
                continue
            seen.add(p2)
            # 尝试相对 base 拼接；也尝试取最后若干段
            candidates = [base / p2, base / Path(p2).name]
            # 去掉常见前缀后拼接
            for prefix in ("nvidia/", "cpp/", "java/", "python/"):
                if p2.startswith(prefix):
                    candidates.append(base / p2[len(prefix) :])
            local = None
            for c in candidates:
                if c.is_file():
                    local = c
                    break
            if local is None:
                skipped += 1
                continue
            try:
                content = local.read_text(encoding="utf-8")
            except Exception:
                skipped += 1
                continue
            _save_file_row(session, repo_name, p2, content)
            stored += 1
            if stored % 50 == 0:
                session.commit()
        session.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "repo_name": repo_name,
            "stored": stored,
            "skipped": skipped,
        }
    except Exception as e:
        session.rollback()
        logger.exception("import path2 failed: %s", e)
        return {"ok": False, "error": str(e), "task_id": task_id}
    finally:
        session.close()
        eng.dispose()


@celery_app.task(name="platform.download_and_save_open_source_code")
def download_and_save_open_source_code(s3_path: str, repo_name: str) -> Dict[str, Any]:
    """从 MinIO/S3 下载对象（文件或前缀）并写入 open_source_code_file。"""
    import zipfile

    from app.core.config import settings
    from app.services.csb_downloader import CSBDownloader
    from app.services.s3_client import effective_access_key, effective_secret_key

    logger.info("download_and_save_open_source_code — s3_path=%s repo=%s", s3_path, repo_name)
    out_root = Path(settings.temp_storage_root) / "open_source" / repo_name
    out_root.mkdir(parents=True, exist_ok=True)

    dl = CSBDownloader(
        app_token=settings.s3_app_token or effective_access_key(),
        region=settings.s3_region,
        bucket_name=settings.s3_bucket_name,
        endpoint_url=settings.s3_endpoint_url or None,
        access_key=settings.s3_access_key or effective_access_key(),
        secret_key=settings.s3_secret_key or effective_secret_key(),
    )

    key = s3_path.lstrip("/")
    prefix = f"{settings.s3_bucket_name}/"
    if key.startswith(prefix):
        key = key[len(prefix) :]

    # 单文件下载；若是 zip 则解压入库
    result = dl.download_single_file(key, str(out_root))
    if not result.get("success") or not result.get("local_path"):
        # 尝试按前缀批量下载
        batch = dl.download_files(bucket_path=key, output_path=str(out_root))
        if not batch.get("success_count"):
            return {
                "ok": False,
                "error": result.get("message") or batch.get("message") or "download failed",
                "s3_path": s3_path,
                "repo_name": repo_name,
            }
        source_dir = out_root
    else:
        local = Path(result["local_path"])
        if local.suffix.lower() == ".zip":
            extract = out_root / "extract"
            extract.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(local, "r") as zf:
                zf.extractall(extract)
            source_dir = extract
        else:
            # 单文件：按相对名写入
            try:
                content = local.read_text(encoding="utf-8")
            except Exception as e:
                return {"ok": False, "error": str(e), "s3_path": s3_path, "repo_name": repo_name}
            eng, session = _sync_session()
            try:
                _save_file_row(session, repo_name, local.name, content, s3_path=s3_path)
                session.commit()
                return {"ok": True, "stored": 1, "repo_name": repo_name, "s3_path": s3_path}
            finally:
                session.close()
                eng.dispose()

    return save_open_source_code_from_local(str(source_dir), repo_name)


@celery_app.task(name="platform.save_open_source_code_from_local")
def save_open_source_code_from_local(source_path: str, repo_name: str) -> Dict[str, Any]:
    eng, session = _sync_session()
    stored = 0
    try:
        root = Path(source_path)
        if not root.exists():
            return {"ok": False, "error": f"source_path not found: {source_path}"}
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".cpp", ".cc", ".h", ".hpp", ".c", ".java", ".py"}:
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = p.relative_to(root).as_posix()
            _save_file_row(session, repo_name, rel, content)
            stored += 1
            if stored % 100 == 0:
                session.commit()
        session.commit()
        return {"ok": True, "repo_name": repo_name, "stored": stored}
    except Exception as e:
        session.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        session.close()
        eng.dispose()


@celery_app.task(name="platform.import_open_source_from_task_path2")
def import_open_source_from_task_path2(
    task_id: str,
    local_base_path: Optional[str] = None,
    repo_name: str = "cpp",
) -> Dict[str, Any]:
    base = local_base_path or settings.open_source_cpp_path
    return _import_path2_from_task(task_id, base, repo_name)


@celery_app.task(name="platform.import_open_source_from_task_path2_nvidia")
def import_open_source_from_task_path2_nvidia(task_id: str, repo_name: str = "nvidia") -> Dict[str, Any]:
    return _import_path2_from_task(task_id, settings.open_source_nvidia_base_path, repo_name)


@celery_app.task(name="platform.import_open_source_from_task_path2_cpp")
def import_open_source_from_task_path2_cpp(task_id: str, repo_name: str = "cpp") -> Dict[str, Any]:
    return _import_path2_from_task(task_id, settings.open_source_cpp_path, repo_name)


@celery_app.task(name="platform.import_open_source_from_task_path2_java")
def import_open_source_from_task_path2_java(task_id: str, repo_name: str = "java") -> Dict[str, Any]:
    return _import_path2_from_task(task_id, settings.open_source_java_path, repo_name)


@celery_app.task(name="platform.import_open_source_from_task_path2_python")
def import_open_source_from_task_path2_python(task_id: str, repo_name: str = "python") -> Dict[str, Any]:
    return _import_path2_from_task(task_id, settings.open_source_python_path, repo_name)


@celery_app.task(name="platform.import_open_source_from_scan_result")
def import_open_source_from_scan_result(task_id: str, repo_name: Optional[str] = None) -> Dict[str, Any]:
    name = repo_name or "cpp"
    return _import_path2_from_task(task_id, settings.open_source_cpp_path, name)
