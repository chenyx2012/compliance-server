"""
OAT 规则配置模型。

前端可通过 /platform/oat-rules 接口 CRUD 管理规则配置。
每条记录保存一份完整的 OAT XML 规则内容，在触发 S1 扫描时以
-oatconfig 参数传入 oat_python，叠加到其内置 OAT-Default.xml 之上。
xml_content 为 NULL 表示仅使用 oat_python 内置默认规则，不做任何叠加。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OatRuleConfig(Base):
    """
    OAT 规则配置表（oat_rule_config）。

    每条记录对应一套可复用的 OAT XML 规则配置，供前端在提交任务时选择。
    选中的规则在 S1 扫描时通过 -oatconfig 传给 oat_python，与内置
    OAT-Default.xml 合并后生效；未选则直接使用内置默认规则。
    """

    __tablename__ = "oat_rule_config"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="规则配置名称（唯一）",
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        default=None,
        comment="规则配置描述",
    )
    xml_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="OAT XML 规则内容；NULL 表示仅使用 oat_python 内置默认规则",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用（false 时前端不可选用）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, comment="创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        comment="最后更新时间（UTC）",
    )

    def __repr__(self) -> str:
        return (
            f"<OatRuleConfig(id={self.id}, name={self.name!r}, "
            f"is_active={self.is_active}, has_xml={self.xml_content is not None})>"
        )
