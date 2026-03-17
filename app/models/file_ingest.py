"""
文件拉取/解析结果持久化模型。

将 /files/ingest 解析得到的目录树（path, next, content）与 meta 存入 MySQL。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FileIngestResult(Base):
    """单次文件拉取并解析后的目录树与元数据。"""

    __tablename__ = "file_ingest_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 来源类型：url / upload
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 来源简述（如 URL、文件名），便于检索
    source_label: Mapped[str] = mapped_column(String(512), nullable=True)
    # 解析得到的元信息（source, type, url, filename 等）
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    # 完整目录树：{ path, next: { ... }, content: null | {...} }
    tree: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    s3_upload_status: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # 0: pending, 1: success, 2: failed

    def __repr__(self) -> str:
        return f"<FileIngestResult(id={self.id}, source_type={self.source_type}, created_at={self.created_at})>"
