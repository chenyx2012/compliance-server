from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.celery_app import celery_app
from app.services.file_ingest import save_code_tree

logger = logging.getLogger(__name__)


@celery_app.task(name="platform.save_code_tree_task")
def save_code_tree_task(
    directory_path: str,
    ingest_id: Optional[int] = None,
    task_id: Optional[str] = None,
    keep_root_prefix: bool = False,
) -> Dict[str, Any]:
    root = Path(directory_path)
    if not root.exists():
        return {"ok": False, "error": f"directory not found: {directory_path}"}
    n = save_code_tree(root, ingest_id, task_id, keep_root_prefix=keep_root_prefix)
    return {"ok": True, "files_stored": n, "task_id": task_id, "ingest_id": ingest_id}
