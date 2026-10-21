from __future__ import annotations

import csv
import logging
import shutil
from pathlib import Path
from typing import Any, Dict

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.sync_db import sync_session
from app.core.time_utils import beijing_now

logger = logging.getLogger(__name__)


def _cleanup_local_scan_csv(csv_path: Path, task_id: str) -> None:
    """解析入库后删除本地临时 CSV；目录在 S3_DOWNLOAD_PATH 下则整任务目录一起清。"""
    try:
        download_root = Path(settings.s3_download_path).resolve()
        path = csv_path.resolve()
        if path.exists():
            path.unlink(missing_ok=True)
        # temp/scan_results/<task_id>/...
        task_dir = path.parent
        try:
            task_dir.relative_to(download_root)
        except ValueError:
            return
        if task_dir.name == task_id or task_dir == download_root / task_id:
            shutil.rmtree(task_dir, ignore_errors=True)
            logger.info("cleaned local scan_results dir — %s", task_dir)
    except Exception as e:
        logger.warning("cleanup local scan csv failed: %s", e)


@celery_app.task(name="platform.parse_csv_and_save")
def parse_csv_and_save(csv_path: str, task_id: str) -> Dict[str, Any]:
    """
    解析 SSCD CSV（path1,start1,end1,path2,start2,end2,similarity）并入库。
    行号按规格 +1 后写入。权威数据在 DB / MinIO；本地文件解析后删除。
    """
    from app.models.scan_result_code_clone import ScanResultCodeClone

    path = Path(csv_path)
    if not path.exists() or path.suffix.lower() != ".csv":
        return {"ok": False, "error": f"csv not found or invalid: {csv_path}"}

    inserted = 0
    try:
        with sync_session() as session:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        start1 = int(row.get("start1") or 0) + 1
                        end1 = int(row.get("end1") or 0) + 1
                        start2 = int(row.get("start2") or 0) + 1
                        end2 = int(row.get("end2") or 0) + 1
                        similarity = float(row.get("similarity") or 0)
                    except (TypeError, ValueError):
                        continue
                    session.add(
                        ScanResultCodeClone(
                            task_id=task_id,
                            path1=row.get("path1"),
                            start1=start1,
                            end1=end1,
                            path2=row.get("path2"),
                            start2=start2,
                            end2=end2,
                            similarity=similarity,
                            created_at=beijing_now(),
                        )
                    )
                    inserted += 1
        logger.info("parse_csv_and_save — task_id=%s inserted=%d", task_id, inserted)
        _cleanup_local_scan_csv(path, task_id)
        return {"ok": True, "task_id": task_id, "inserted": inserted}
    except Exception as e:
        logger.exception("parse_csv_and_save failed: %s", e)
        return {"ok": False, "error": str(e), "task_id": task_id}
