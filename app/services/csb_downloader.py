"""
CSB/OBS 对象存储下载（能力等价：标准 S3 / MinIO / 华为 OBS S3 API）。

入参/出参语义对齐规格 §13.5；凭据经环境注入。
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CSBDownloader:
    def __init__(
        self,
        *,
        app_token: str = "",
        region: str = "cn-east-3",
        bucket_name: str = "",
        bucket_id: Optional[str] = None,
        vendor: str = "HEC",
        shared: bool = False,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        retry_times: int = 3,
        thread_num: int = 12,
        big_file_threshold: int = 100 * 1024 * 1024,
    ) -> None:
        self.app_token = app_token or ""
        self.region = region
        self.bucket_name = bucket_name
        self.bucket_id = bucket_id
        self.vendor = vendor
        self.shared = shared
        self.endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT_URL") or None
        # access/secret 优先；否则用 app_token 作 access_key，secret 可同值或环境变量
        self.access_key = access_key or os.getenv("S3_ACCESS_KEY") or self.app_token
        self.secret_key = (
            secret_key
            or os.getenv("S3_SECRET_KEY")
            or os.getenv("S3_APP_SECRET")
            or self.app_token
        )
        self.retry_times = max(1, retry_times)
        self.thread_num = max(1, thread_num)
        self.big_file_threshold = big_file_threshold
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            raise RuntimeError("boto3 is required for CSB/S3 download; pip install boto3") from e

        # MinIO 默认 path-style
        path_style = os.getenv("S3_PATH_STYLE", "true").lower() in ("1", "true", "yes")
        cfg = Config(
            retries={"max_attempts": self.retry_times, "mode": "standard"},
            region_name=self.region,
            signature_version="s3v4",
            s3={"addressing_style": "path" if path_style else "auto"},
        )
        kwargs: Dict[str, Any] = {
            "service_name": "s3",
            "region_name": self.region,
            "aws_access_key_id": self.access_key or "unused",
            "aws_secret_access_key": self.secret_key or "unused",
            "config": cfg,
        }
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        self._client = boto3.client(**kwargs)
        return self._client

    def _normalize_key(self, object_key: str) -> str:
        key = (object_key or "").lstrip("/")
        prefix = f"{self.bucket_name}/"
        if key.startswith(prefix):
            key = key[len(prefix) :]
        return key

    def download_single_file(
        self,
        object_key: str,
        output_path: str,
        *,
        local_file_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """下载单个对象到 output_path 目录。"""
        key = self._normalize_key(object_key)
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        file_name = local_file_name or Path(key).name or "download.bin"
        local_path = out_dir / file_name

        last_err: Optional[Exception] = None
        for attempt in range(1, self.retry_times + 1):
            try:
                client = self._get_client()
                # 大文件走 multipart 由 boto3 transfer 自动处理
                client.download_file(self.bucket_name, key, str(local_path))
                size = local_path.stat().st_size if local_path.exists() else 0
                logger.info(
                    "CSB download_single ok — bucket=%s key=%s local=%s size=%d attempt=%d",
                    self.bucket_name,
                    key,
                    local_path,
                    size,
                    attempt,
                )
                return {
                    "success": True,
                    "message": "download success",
                    "total": 1,
                    "success_count": 1,
                    "failed_count": 0,
                    "failed_files": [],
                    "skipped_count": 0,
                    "file_name": file_name,
                    "file_size": size,
                    "object_key": key,
                    "local_path": str(local_path),
                }
            except Exception as e:
                last_err = e
                logger.warning(
                    "CSB download_single retry — attempt=%d/%d key=%s err=%s",
                    attempt,
                    self.retry_times,
                    key,
                    e,
                )
        return {
            "success": False,
            "message": f"download failed: {last_err}",
            "total": 1,
            "success_count": 0,
            "failed_count": 1,
            "failed_files": [key],
            "skipped_count": 0,
            "file_name": file_name,
            "file_size": 0,
            "object_key": key,
            "local_path": None,
        }

    def _list_keys(self, bucket_path: str) -> List[str]:
        client = self._get_client()
        prefix = (bucket_path or "").lstrip("/")
        keys: List[str] = []
        token = None
        while True:
            kwargs: Dict[str, Any] = {"Bucket": self.bucket_name, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents") or []:
                k = obj.get("Key")
                if k and not k.endswith("/"):
                    keys.append(k)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return keys

    def download_files(
        self,
        *,
        bucket_path: str = "",
        output_path: str = "temp/scan_results",
        include_file_name: Optional[str] = None,
        exclude_file_name: Optional[str] = None,
        incremental: bool = False,
    ) -> Dict[str, Any]:
        """按前缀列出并并发下载。"""
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            keys = self._list_keys(bucket_path)
        except Exception as e:
            logger.exception("CSB list failed: %s", e)
            return {
                "success": False,
                "message": f"list failed: {e}",
                "total": 0,
                "success_count": 0,
                "failed_count": 0,
                "failed_files": [],
                "skipped_count": 0,
            }

        filtered: List[str] = []
        skipped = 0
        for k in keys:
            name = Path(k).name
            if include_file_name and include_file_name not in name:
                skipped += 1
                continue
            if exclude_file_name and exclude_file_name in name:
                skipped += 1
                continue
            local = out_dir / name
            if incremental and local.exists():
                skipped += 1
                continue
            filtered.append(k)

        success_count = 0
        failed_files: List[str] = []

        def _one(key: str) -> Tuple[str, bool, str]:
            r = self.download_single_file(key, str(out_dir))
            return key, bool(r.get("success")), str(r.get("message") or "")

        with ThreadPoolExecutor(max_workers=self.thread_num) as pool:
            futs = [pool.submit(_one, k) for k in filtered]
            for fut in as_completed(futs):
                key, ok, _msg = fut.result()
                if ok:
                    success_count += 1
                else:
                    failed_files.append(key)

        return {
            "success": len(failed_files) == 0,
            "message": "download completed",
            "total": len(filtered),
            "success_count": success_count,
            "failed_count": len(failed_files),
            "failed_files": failed_files,
            "skipped_count": skipped,
        }
