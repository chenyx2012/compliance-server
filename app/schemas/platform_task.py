"""
平台任务相关 Pydantic 模型。

- PlatformTaskResponse    : 任务详情响应
- PlatformTaskListResponse: 分页列表响应
- ServiceStatusUpdate     : 扫描服务回调更新请求
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

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
    ingest_id: Optional[int]
    task_status: TaskStatus
    s1_status: ServiceStatus
    s2_status: ServiceStatus
    s3_status: ServiceStatus
    s4_status: ServiceStatus
    s5_status: ServiceStatus
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
