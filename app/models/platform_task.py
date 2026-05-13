"""
平台任务记录模型。

在 POST /platform/tasks 触发后写入，记录每次任务提交的基本信息
与五个扫描服务（S1/S2/S3/S4/S5）的状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_task_id() -> str:
    """生成带 pt- 前缀的 UUID4 作为平台任务唯一标识。"""
    return f"pt-{uuid.uuid4().hex}"


class PlatformTask(Base):
    """
    平台任务记录表。

    每次调用 POST /platform/tasks 时创建一条记录，记录：
    - task_id     : 自动生成的唯一任务标识（pt-<uuid4>）
    - task_name   : 用户提交的任务名称
    - ingest_id   : 关联的文件入库记录 ID（字符串形式的 FileIngestResult.id）
    - task_status : 任务整体状态
                    active    — 正常运行中（默认）
                    completed — 所有选中服务均已完成
                    failed    — 至少一个服务失败
                    deleted   — 软删除（逻辑删除，保留数据）
    - s1_status / s2_status / s3_status / s4_status / s5_status:
                  各扫描服务的执行状态
                  pending / running / success / failed / skipped
    - deleted_at  : 软删除时间戳，为 NULL 表示未删除
    """

    __tablename__ = "platform_task"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    task_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        default=_gen_task_id,
        index=True,
        comment="平台任务唯一标识，格式 pt-<uuid4hex>",
    )
    task_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="用户提交的任务名称"
    )
    ingest_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True, comment="关联 file_ingest_result.id（字符串）"
    )
    # sentry 侧分析任务 ID（mission/upload 或 mission/git 返回的 analysis_id）
    s3_analysis_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        index=True,
        comment="compliance-sentry analysis_id，由 mission 提交后写入，用于轮询扫描进度",
    )
    # sentry 扫描结果摘要（扫描完成后写入）
    s3_has_conflicts: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
        comment="S3 扫描是否检测到许可证冲突（sentry has_conflicts）；NULL 表示未获取到",
    )
    s3_conflict_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="S3 扫描检测到的许可证冲突数量",
    )

    # -----------------------------------------------------------------------
    # 任务整体状态
    # active / completed / failed / deleted
    # -----------------------------------------------------------------------
    task_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        index=True,
        comment="任务整体状态：active / completed / failed / deleted",
    )

    # -----------------------------------------------------------------------
    # 五个扫描服务状态
    # 取值：pending / running / success / failed / skipped
    # -----------------------------------------------------------------------
    s1_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="skipped",
        comment="S1 扫描服务状态",
    )
    s2_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="skipped",
        comment="S2 扫描服务状态",
    )
    s3_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="skipped",
        comment="S3 compliance-sentry 扫描服务状态",
    )
    s4_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="skipped",
        comment="S4 扫描服务状态",
    )
    s5_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="skipped",
        comment="S5 扫描服务状态",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, nullable=False, comment="任务创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
        comment="任务最后更新时间（UTC）",
    )
    # 软删除时间戳；非 NULL 表示已被逻辑删除
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
        comment="软删除时间（UTC），NULL 表示未删除",
    )

    def __repr__(self) -> str:
        return (
            f"<PlatformTask(id={self.id}, task_id={self.task_id!r}, "
            f"task_name={self.task_name!r}, task_status={self.task_status!r}, "
            f"s1={self.s1_status}, s2={self.s2_status}, s3={self.s3_status}, "
            f"s4={self.s4_status}, s5={self.s5_status})>"
        )


# ---------------------------------------------------------------------------
# 工具函数（可被多个路由模块复用）
# ---------------------------------------------------------------------------

_SERVICE_FIELDS = ("s1_status", "s2_status", "s3_status", "s4_status", "s5_status")


def derive_task_status(pt: "PlatformTask") -> str:
    """
    根据五个服务的当前状态推导任务整体状态。

    规则：
    - 任意非 skipped 服务为 failed  → failed
    - 所有非 skipped 服务均 success → completed
    - 否则                          → active（仍有服务 pending/running）
    """
    statuses = [getattr(pt, f) for f in _SERVICE_FIELDS]
    active_statuses = [s for s in statuses if s != "skipped"]
    if not active_statuses:
        return "completed"
    if any(s == "failed" for s in active_statuses):
        return "failed"
    if all(s == "success" for s in active_statuses):
        return "completed"
    return "active"

