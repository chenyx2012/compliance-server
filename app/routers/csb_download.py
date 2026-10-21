"""CSB/OBS 下载（S3 兼容实现）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.csb_downloader import CSBDownloader

logger = logging.getLogger(__name__)
router = APIRouter(tags=["csb-download"])


class CsbDownloadBody(BaseModel):
    app_token: Optional[str] = None
    region: Optional[str] = None
    bucket_name: Optional[str] = None
    bucket_id: Optional[str] = None
    vendor: Optional[str] = None
    shared: Optional[bool] = None
    output_path: Optional[str] = None
    retry_times: Optional[int] = None
    thread_num: int = Field(default=12)
    big_file_threshold: int = Field(default=100 * 1024 * 1024)
    bucket_path: Optional[str] = None
    include_file_name: Optional[str] = None
    exclude_file_name: Optional[str] = None
    incremental: Optional[bool] = None
    object_key: Optional[str] = None


def _downloader(body: CsbDownloadBody) -> CSBDownloader:
    return CSBDownloader(
        app_token=body.app_token or settings.s3_app_token,
        region=body.region or settings.s3_region,
        bucket_name=body.bucket_name or settings.s3_bucket_name,
        bucket_id=body.bucket_id or settings.s3_bucket_id,
        vendor=body.vendor or settings.s3_vendor,
        shared=settings.s3_shared if body.shared is None else body.shared,
        endpoint_url=settings.s3_endpoint_url or None,
        access_key=settings.s3_access_key or None,
        secret_key=settings.s3_secret_key or None,
        retry_times=body.retry_times or 3,
        thread_num=body.thread_num,
        big_file_threshold=body.big_file_threshold,
    )


@router.post("/api/s3/download/csb")
async def download_csb(body: CsbDownloadBody) -> Dict[str, Any]:
    try:
        dl = _downloader(body)
        if not dl.bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured")
        return dl.download_files(
            bucket_path=body.bucket_path or settings.s3_bucket_path,
            output_path=body.output_path or settings.s3_download_path,
            include_file_name=body.include_file_name,
            exclude_file_name=body.exclude_file_name,
            incremental=bool(body.incremental),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.post("/api/s3/download/csb/single")
async def download_csb_single(body: CsbDownloadBody) -> Dict[str, Any]:
    try:
        if not body.object_key:
            raise ValueError("object_key is required")
        dl = _downloader(body)
        if not dl.bucket_name:
            raise ValueError("S3_BUCKET_NAME not configured")
        return dl.download_single_file(
            body.object_key,
            body.output_path or settings.s3_download_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
