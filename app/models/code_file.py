"""代码文件表（入库后按白名单落库）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now

_MediumText = Text().with_variant(MEDIUMTEXT(), "mysql")


class CodeFile(Base):
    __tablename__ = "code_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingest_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("file_ingest_result.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(_MediumText, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extension: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)
