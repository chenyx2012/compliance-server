"""开源代码文件表。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now

_LongText = Text().with_variant(LONGTEXT(), "mysql")


class OpenSourceCodeFile(Base):
    __tablename__ = "open_source_code_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    content: Mapped[Optional[str]] = mapped_column(_LongText, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extension: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    s3_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    remote_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)
