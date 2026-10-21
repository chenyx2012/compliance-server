"""
文件拉取/解析结果持久化模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now


class FileIngestResult(Base):
    """单次文件拉取并解析后的元数据（目录树不再强制入库，见 code_file）。"""

    __tablename__ = "file_ingest_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_label: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 兼容旧库：保留可选 tree；新流程可不写
    tree: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    s3_upload_status: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 pending / 1 success / 2 failed

    def __repr__(self) -> str:
        return f"<FileIngestResult(id={self.id}, source_type={self.source_type}, created_at={self.created_at})>"
