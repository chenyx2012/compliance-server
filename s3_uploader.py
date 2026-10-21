#!/usr/bin/env python3
"""
S3/MinIO 兼容上传脚本（规格 §7.4 命令模板）。

用法:
  python s3_uploader.py \\
    --local_folder_absolute_path=<abs> \\
    --app_token=<token> \\
    --region=<region> \\
    --bucket_name=<bucket> \\
    --bucket_path=<prefix/> \\
    --show_speed

环境变量：S3_ENDPOINT_URL / S3_ACCESS_KEY / S3_SECRET_KEY / S3_PATH_STYLE
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _client(region: str, app_token: str):
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("S3_ENDPOINT_URL") or None
    access = os.getenv("S3_ACCESS_KEY") or app_token
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("S3_APP_SECRET") or app_token
    path_style = os.getenv("S3_PATH_STYLE", "true").lower() in ("1", "true", "yes")
    kwargs = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": access or "unused",
        "aws_secret_access_key": secret or "unused",
        "config": Config(
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
            s3={"addressing_style": "path" if path_style else "auto"},
        ),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(**kwargs)


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        try:
            client.create_bucket(Bucket=bucket)
            print(f"[init] created bucket {bucket}")
        except Exception as e:
            print(f"[warn] create bucket failed: {e}", file=sys.stderr)


def _upload_file(client, bucket: str, key: str, local: Path, show_speed: bool) -> None:
    size = local.stat().st_size
    t0 = time.time()
    client.upload_file(str(local), bucket, key)
    elapsed = max(time.time() - t0, 1e-6)
    if show_speed:
        mbps = (size / (1024 * 1024)) / elapsed
        print(f"[upload] {local.name} -> s3://{bucket}/{key} ({size} bytes, {mbps:.2f} MB/s)")
    else:
        print(f"[upload] {local.name} -> s3://{bucket}/{key}")


def main() -> int:
    parser = argparse.ArgumentParser(description="S3/MinIO compatible uploader")
    parser.add_argument("--local_folder_absolute_path", required=True)
    parser.add_argument("--app_token", required=True)
    parser.add_argument("--region", default="cn-east-3")
    parser.add_argument("--bucket_name", required=True)
    parser.add_argument("--bucket_path", default="csv/")
    parser.add_argument("--show_speed", action="store_true")
    args = parser.parse_args()

    local = Path(args.local_folder_absolute_path)
    if not local.exists():
        print(f"ERROR: path not found: {local}", file=sys.stderr)
        return 1

    bucket_path = args.bucket_path or ""

    try:
        client = _client(args.region, args.app_token)
    except ImportError:
        print("ERROR: boto3 is required; pip install boto3", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: create s3 client failed: {e}", file=sys.stderr)
        return 1

    try:
        _ensure_bucket(client, args.bucket_name)
        if local.is_file():
            key = f"{bucket_path}{local.name}"
            _upload_file(client, args.bucket_name, key, local, args.show_speed)
            return 0

        files = [p for p in local.rglob("*") if p.is_file()]
        if not files:
            print(f"WARNING: empty directory {local}")
            return 0
        for p in files:
            rel = p.relative_to(local).as_posix()
            key = f"{bucket_path}{local.name}/{rel}"
            _upload_file(client, args.bucket_name, key, p, args.show_speed)
        print(f"[done] uploaded {len(files)} files under {local.name}")
        return 0
    except Exception as e:
        print(f"ERROR: upload failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
