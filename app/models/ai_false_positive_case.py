"""AI 误报案例表。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import beijing_now


class AiFalsePositiveCase(Base):
    __tablename__ = "ai_false_positive_cases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    code_snippet_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    judgment_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    case_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=beijing_now, onupdate=beijing_now, nullable=False
    )
