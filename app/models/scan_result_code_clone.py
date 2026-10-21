"""代码相似度（克隆）扫描结果表。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now


class ScanResultCodeClone(Base):
    __tablename__ = "scan_result_code_clone"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    path1: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    start1: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end1: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    path2: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    start2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    ai_judgment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ai_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)
