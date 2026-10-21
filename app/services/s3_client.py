"""
统一 S3/MinIO 客户端（上传/下载/建桶/健康检查）。

凭据优先级：S3_ACCESS_KEY/SECRET → S3_UPLOADER_TOKEN / S3_APP_TOKEN。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


def effective_access_key() -> str:
    return (
        settings.s3_access_key
        or settings.s3_uploader_token
        or settings.s3_app_token
        or ""
    )


def effective_secret_key() -> str:
    return (
        settings.s3_secret_key
        or settings.s3_uploader_token
        or settings.s3_app_token
        or effective_access_key()
    )


def s3_configured() -> bool:
    return bool(effective_access_key() and settings.effective_s3_uploader_bucket_name)


def subprocess_s3_env() -> Dict[str, str]:
    """注入子进程，保证 s3_uploader.py 能读到 MinIO 配置。"""
    env = os.environ.copy()
    if settings.s3_endpoint_url:
        env["S3_ENDPOINT_URL"] = settings.s3_endpoint_url
    ak = effective_access_key()
    sk = effective_secret_key()
    if ak:
        env["S3_ACCESS_KEY"] = ak
    if sk:
        env["S3_SECRET_KEY"] = sk
    return env


def get_s3_client():
    import boto3
    from botocore.client import Config

    addressing = "path" if settings.s3_path_style else "auto"
    cfg = Config(
        signature_version="s3v4",
        s3={"addressing_style": addressing},
        retries={"max_attempts": 3, "mode": "standard"},
    )
    kwargs: Dict[str, Any] = {
        "service_name": "s3",
        "region_name": settings.effective_s3_uploader_region or settings.s3_region,
        "aws_access_key_id": effective_access_key() or "unused",
        "aws_secret_access_key": effective_secret_key() or "unused",
        "config": cfg,
    }
    endpoint = settings.s3_endpoint_url or None
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(**kwargs)


def ensure_bucket(bucket: Optional[str] = None) -> bool:
    bucket = bucket or settings.effective_s3_uploader_bucket_name
    if not bucket:
        return False
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except Exception:
        try:
            # MinIO / 部分区域 CreateBucket 不需要 LocationConstraint
            client.create_bucket(Bucket=bucket)
            logger.info("created bucket %s", bucket)
            return True
        except Exception as e:
            logger.warning("ensure_bucket failed bucket=%s err=%s", bucket, e)
            return False


def upload_file(local_path: Path, *, key: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    上传单文件。返回 (error_msg, s3_path)。
    s3_path 格式：{bucket}/{bucket_path}{filename}
    """
    if not s3_configured():
        logger.info("s3 upload skip — credentials/bucket not configured")
        return None, None

    bucket = settings.effective_s3_uploader_bucket_name
    bucket_path = settings.effective_s3_uploader_bucket_path
    local_path = local_path.resolve()
    if not local_path.is_file():
        return f"not a file: {local_path}", None

    object_key = key or f"{bucket_path}{local_path.name}"
    s3_path = f"{bucket}/{object_key}"
    try:
        ensure_bucket(bucket)
        client = get_s3_client()
        client.upload_file(str(local_path), bucket, object_key)
        logger.info("s3 upload ok — %s -> %s", local_path.name, s3_path)
        return None, s3_path
    except Exception as e:
        logger.exception("s3 upload failed: %s", e)
        return str(e), None


def upload_directory(local_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    """上传目录下所有文件；返回 (error, s3_path 前缀)。"""
    if not s3_configured():
        return None, None
    local_dir = local_dir.resolve()
    if not local_dir.is_dir():
        return f"not a directory: {local_dir}", None

    bucket = settings.effective_s3_uploader_bucket_name
    bucket_path = settings.effective_s3_uploader_bucket_path
    prefix = f"{bucket_path}{local_dir.name}"
    s3_path = f"{bucket}/{prefix}"
    try:
        ensure_bucket(bucket)
        client = get_s3_client()
        files = [p for p in local_dir.rglob("*") if p.is_file()]
        for p in files:
            rel = p.relative_to(local_dir).as_posix()
            key = f"{prefix}/{rel}"
            client.upload_file(str(p), bucket, key)
        logger.info("s3 upload dir ok — files=%d s3_path=%s", len(files), s3_path)
        return None, s3_path
    except Exception as e:
        logger.exception("s3 upload dir failed: %s", e)
        return str(e), None


def health_check() -> Dict[str, Any]:
    if not s3_configured():
        return {"ok": False, "configured": False, "detail": "S3 credentials/bucket not set"}
    try:
        client = get_s3_client()
        bucket = settings.effective_s3_uploader_bucket_name
        client.head_bucket(Bucket=bucket)
        return {
            "ok": True,
            "configured": True,
            "endpoint": settings.s3_endpoint_url or "(default aws)",
            "bucket": bucket,
        }
    except Exception as e:
        return {
            "ok": False,
            "configured": True,
            "endpoint": settings.s3_endpoint_url or "(default aws)",
            "bucket": settings.effective_s3_uploader_bucket_name,
            "detail": str(e),
        }
