"""
OAT 扫描结果模型。

每次 S1 扫描（oat_python 执行）完成后写入一条记录，
与 platform_task 通过 platform_task_id 关联。

三类 issue 的详情以 JSON 数组单独存储，格式：
  [{"file": "路径", "content": "类型/许可证/版权内容", "project": "项目名"}, ...]
  - invalid_file_type_issues          : Invalid File Type 问题列表
  - license_header_invalid_issues     : License Header Invalid 问题列表
  - copyright_header_invalid_issues   : Copyright Header Invalid 问题列表
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OatScanResult(Base):
    """
    OAT 扫描结果表（oat_scan_result）。

    字段说明：
    - platform_task_id           : 关联 platform_task.task_id
    - rule_config_id             : 使用的规则配置 ID（NULL = 内置默认规则）
    - status                     : running / success / failed
    - exit_code                  : oat_python 进程退出码（0=无问题，1=有issue，负数=崩溃）
    - total_issues               : 全部 issue 总数（三类之和）
    - invalid_file_type_count    : 二进制/归档文件类型问题数
    - license_header_invalid_count  : License 头缺失/不合规数
    - copyright_header_invalid_count: Copyright 头缺失/不合规数
    - invalid_file_type_issues          : Invalid File Type 问题 JSON 数组
    - license_header_invalid_issues     : License Header Invalid 问题 JSON 数组
    - copyright_header_invalid_issues   : Copyright Header Invalid 问题 JSON 数组
    - report_text                : PlainReport_*.txt 完整内容（MEDIUMTEXT）
    - error_message              : 执行异常时的错误信息
    """

    __tablename__ = "oat_scan_result"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    platform_task_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="关联 platform_task.task_id",
    )
    rule_config_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        comment="使用的 OAT 规则配置 ID；NULL 表示使用内置默认规则",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="running",
        comment="扫描状态：running / success / failed",
    )
    exit_code: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        comment="oat_python 进程退出码；0=无问题，1=有issue，NULL=尚未完成",
    )
    total_issues: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="issue 总数（三类之和）"
    )
    invalid_file_type_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Invalid File Type 问题数"
    )
    license_header_invalid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="License Header Invalid 问题数"
    )
    copyright_header_invalid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Copyright Header Invalid 问题数"
    )
    # ── 三类问题详情 JSON（扫描完成后由 _parse_report_issues 填充）─────────────
    # 新建表自动生效；若表已存在需执行：
    #   ALTER TABLE oat_scan_result
    #     ADD COLUMN invalid_file_type_issues JSON NULL,
    #     ADD COLUMN license_header_invalid_issues JSON NULL,
    #     ADD COLUMN copyright_header_invalid_issues JSON NULL;
    invalid_file_type_issues: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        comment="Invalid File Type 问题详情 [{file, content, project}, ...]",
    )
    license_header_invalid_issues: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        comment="License Header Invalid 问题详情 [{file, content, project}, ...]",
    )
    copyright_header_invalid_issues: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        comment="Copyright Header Invalid 问题详情 [{file, content, project}, ...]",
    )

    celery_task_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        default=None,
        comment="异步模式下对应的 Celery task id，用于取消/撤销任务",
    )
    report_text: Mapped[Optional[str]] = mapped_column(
        # MEDIUMTEXT：MySQL 最大 16 MB，完整保存 PlainReport_*.txt 三类问题详情
        # 新建表时自动生效；若表已存在，需手动执行：
        #   ALTER TABLE oat_scan_result MODIFY COLUMN report_text MEDIUMTEXT;
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=True,
        default=None,
        comment="PlainReport_*.txt 完整内容（三类 issue 详情，MySQL MEDIUMTEXT 最大 16MB）",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        String(2000),
        nullable=True,
        default=None,
        comment="执行异常时的错误信息",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
        comment="记录最后更新时间（UTC）",
    )

    def __repr__(self) -> str:
        return (
            f"<OatScanResult(id={self.id}, platform_task_id={self.platform_task_id!r}, "
            f"status={self.status!r}, total_issues={self.total_issues})>"
        )
