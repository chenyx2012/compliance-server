"""
平台任务相关 Pydantic 模型。

- PlatformTaskResponse    : 任务详情响应
- PlatformTaskListResponse: 分页列表响应
- ServiceStatusUpdate     : 扫描服务回调更新请求
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 枚举字面量类型
# ---------------------------------------------------------------------------

TaskStatus = Literal["active", "completed", "failed", "deleted"]
ServiceStatus = Literal["pending", "running", "success", "failed", "skipped"]
ServiceName = Literal["S1", "S2", "S3", "S4", "S5"]


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------

class PlatformTaskResponse(BaseModel):
    """单条平台任务详情。"""

    id: int
    task_id: str
    task_name: str
    ingest_id: Optional[str]
    task_status: TaskStatus
    s1_status: ServiceStatus
    s2_status: ServiceStatus
    s3_status: ServiceStatus
    s4_status: ServiceStatus
    s5_status: ServiceStatus
    # compliance-sentry 侧分析任务 ID，由 mission 提交成功后写入
    s3_analysis_id: Optional[str] = Field(None, description="compliance-sentry analysis_id，可用于直接查询 sentry 扫描进度")
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]

    model_config = {"from_attributes": True}


class PlatformTaskListResponse(BaseModel):
    """任务列表分页响应。"""

    total: int = Field(..., description="满足条件的总记录数")
    page: int = Field(..., description="当前页码（从 1 开始）")
    page_size: int = Field(..., description="每页条数")
    items: List[PlatformTaskResponse]


# ---------------------------------------------------------------------------
# 服务状态更新请求（供后端五个扫描服务回调）
# ---------------------------------------------------------------------------

class ServiceStatusUpdateRequest(BaseModel):
    """
    扫描服务回调请求体。

    后端每个扫描服务（S1~S5）在完成/失败后 PATCH 此接口，
    告知主任务自己的最新状态。
    """

    service: ServiceName = Field(..., description="服务标识：S1 / S2 / S3 / S4 / S5")
    status: ServiceStatus = Field(
        ...,
        description="服务最新状态：running / success / failed / skipped",
    )
    message: Optional[str] = Field(
        None,
        max_length=500,
        description="可选的附加说明（如失败原因）",
    )


class ServiceStatusUpdateResponse(BaseModel):
    """状态更新响应。"""

    task_id: str
    service: ServiceName
    service_status: ServiceStatus
    task_status: TaskStatus
    updated_at: datetime


# ---------------------------------------------------------------------------
# 首页看板
# ---------------------------------------------------------------------------

class MonitorProjectStats(BaseModel):
    """监控项目统计指标（支持环比）。"""

    current: int = Field(..., description="本月总数")
    last_month: int = Field(..., description="上月总数")
    change: int = Field(..., description="环比变化量（本月 - 上月）")
    change_rate: float = Field(
        0.0, description="环比变化率（百分比，上月为 0 时返回 0.0）"
    )


class DashboardResponse(BaseModel):
    """首页看板汇总数据。"""

    month: str = Field(..., description="当前统计月份，格式 YYYY-MM")
    monitor_projects: MonitorProjectStats = Field(..., description="监控项目数统计")


# ---------------------------------------------------------------------------
# 风险统计看板（环比）
# ---------------------------------------------------------------------------

class ServiceRiskStats(BaseModel):
    """单个服务的风险数统计（含环比）。"""

    current: int = Field(0, description="本月风险任务数")
    last_month: int = Field(0, description="上月风险任务数")
    change: int = Field(0, description="环比变化量（本月 - 上月）")
    change_rate: float = Field(
        0.0, description="环比变化率（百分比，上月为 0 时返回 0.0）"
    )
    integrated: bool = Field(True, description="该服务是否已接入，false 表示预留占位")


class AllServicesRiskStats(BaseModel):
    """五个扫描服务各自的风险统计。"""

    s1: ServiceRiskStats = Field(description="S1 OAT 开源合规扫描")
    s2: ServiceRiskStats = Field(description="S2（预留）")
    s3: ServiceRiskStats = Field(description="S3 compliance-sentry 许可证兼容性扫描")
    s4: ServiceRiskStats = Field(description="S4（预留）")
    s5: ServiceRiskStats = Field(description="S5（预留）")


class RiskOverviewResponse(BaseModel):
    """总体风险数看板响应。"""

    month: str = Field(..., description="当前统计月份，格式 YYYY-MM")
    total: ServiceRiskStats = Field(..., description="所有服务风险总和（含环比）")
    by_service: AllServicesRiskStats = Field(..., description="各服务风险明细")


class PendingRisksResponse(BaseModel):
    """待处理（进行中）扫描任务看板响应。"""

    month: str = Field(..., description="当前统计月份，格式 YYYY-MM")
    total: ServiceRiskStats = Field(..., description="所有服务待处理任务总和（含环比）")
    by_service: AllServicesRiskStats = Field(..., description="各服务待处理任务明细")


# ---------------------------------------------------------------------------
# 合规趋势（最近 6 个月）
# ---------------------------------------------------------------------------

class TrendServiceData(BaseModel):
    """单月单服务的扫描量与风险量。"""

    scans: int = Field(0, description="已完成扫描任务数（success + failed）")
    risks: int = Field(0, description="检测到风险的扫描任务数")


class TrendMonthData(BaseModel):
    """单月合规趋势数据。"""

    month: str = Field(..., description="月份，格式 YYYY-MM")
    total_scans: int = Field(0, description="当月所有服务合计完成扫描数")
    risk_count: int = Field(0, description="当月所有服务合计风险数")
    risk_rate: Optional[float] = Field(
        None, description="风险占比百分比（risk_count / total_scans × 100）；无扫描时为 null"
    )
    by_service: Dict[str, TrendServiceData] = Field(
        default_factory=dict, description="各服务（s1/s2/s3/s4/s5）扫描与风险明细"
    )


class ComplianceTrendResponse(BaseModel):
    """最近 6 个月合规趋势看板响应。"""

    months: List[TrendMonthData] = Field(..., description="最近 6 个月数据，从最早到最近排列")


# ---------------------------------------------------------------------------
# OAT 风险类型分布（饼图）
# ---------------------------------------------------------------------------

class OatRiskTypeItem(BaseModel):
    """单个 OAT 风险类型的统计项。"""

    type: str = Field(..., description="风险类型标识：invalid_file_type / license_header_invalid / copyright_header_invalid")
    label: str = Field(..., description="中文展示名称，供前端饼图直接使用")
    count: int = Field(0, description="该风险类型在统计范围内的累计问题数")
    rate: float = Field(0.0, description="占全部风险问题的百分比（保留两位小数）")


class OatRiskPieResponse(BaseModel):
    """OAT S1 风险类型分布看板响应（饼图数据）。"""

    total: int = Field(0, description="三类风险问题总数")
    scan_count: int = Field(0, description="纳入统计的成功扫描任务数")
    month: Optional[str] = Field(None, description="若按月筛选则返回月份（YYYY-MM），否则为 null 表示全量统计")
    items: List[OatRiskTypeItem] = Field(..., description="三个风险类型的明细列表")
