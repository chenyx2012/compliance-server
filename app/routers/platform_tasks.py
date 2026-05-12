"""
平台任务管理接口。

  GET  /platform/dashboard                首页看板（监控项目总数 + 环比）
  GET  /platform/tasks/query              任务多条件查询（分页）
  GET  /platform/tasks/{task_id}          根据 task_id 查询单条任务
  PATCH /platform/tasks/{task_id}/service-status   扫描服务回调更新状态
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.platform_task import PlatformTask, derive_task_status
from app.schemas.platform_task import (
    DashboardResponse,
    MonitorProjectStats,
    PlatformTaskListResponse,
    PlatformTaskResponse,
    ServiceStatusUpdateRequest,
    ServiceStatusUpdateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["platform-tasks"])


# ===========================================================================
# GET /platform/dashboard  — 首页看板
# ===========================================================================

@router.get(
    "/platform/dashboard",
    response_model=DashboardResponse,
    summary="首页看板：监控项目总数及环比涨跌",
    tags=["platform-dashboard"],
)
async def platform_dashboard(
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    """
    返回首页看板核心指标：

    - **monitor_projects.current**     : 本月（自然月）新增监控项目数
    - **monitor_projects.last_month**  : 上月新增监控项目数
    - **monitor_projects.change**      : 环比变化量（本月 - 上月）
    - **monitor_projects.change_rate** : 环比变化率（%），上月为 0 时返回 null

    统计口径：`platform_task` 表中 `task_status != 'deleted'` 的记录，
    按 `created_at` 所在自然月分组计数。
    """
    now = datetime.now(timezone.utc)
    # 本月第一天 00:00:00 UTC
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 上月第一天
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)

    base_filter = PlatformTask.task_status != "deleted"

    current_count_result = await db.execute(
        select(func.count())
        .select_from(PlatformTask)
        .where(
            and_(
                base_filter,
                PlatformTask.created_at >= this_month_start,
            )
        )
    )
    current_count: int = current_count_result.scalar_one()

    last_month_count_result = await db.execute(
        select(func.count())
        .select_from(PlatformTask)
        .where(
            and_(
                base_filter,
                PlatformTask.created_at >= last_month_start,
                PlatformTask.created_at < this_month_start,
            )
        )
    )
    last_month_count: int = last_month_count_result.scalar_one()

    change = current_count - last_month_count
    change_rate: Optional[float] = (
        round(change / last_month_count * 100, 2) if last_month_count != 0 else None
    )

    month_str = now.strftime("%Y-%m")
    logger.info(
        "platform_dashboard — month=%s current=%d last_month=%d change=%d change_rate=%s",
        month_str, current_count, last_month_count, change, change_rate,
    )

    return DashboardResponse(
        month=month_str,
        monitor_projects=MonitorProjectStats(
            current=current_count,
            last_month=last_month_count,
            change=change,
            change_rate=change_rate,
        ),
    )


# ===========================================================================
# GET /platform/tasks/query  — 多条件查询（分页）
# ===========================================================================

@router.get(
    "/platform/tasks/query",
    response_model=PlatformTaskListResponse,
    summary="平台任务多条件查询（分页）",
    tags=["platform-tasks"],
)
async def query_platform_tasks(
    # --- 筛选条件 ---
    task_id: Optional[str] = Query(None, description="按 task_id 精确查询"),
    task_name: Optional[str] = Query(None, description="任务名称模糊匹配（含此字符串即命中）"),
    task_status: Optional[str] = Query(
        None, description="任务整体状态过滤：active / completed / failed / deleted"
    ),
    ingest_id: Optional[int] = Query(None, description="按关联 ingest_id 查询"),
    s1_status: Optional[str] = Query(None, description="S1 服务状态过滤"),
    s2_status: Optional[str] = Query(None, description="S2 服务状态过滤"),
    s3_status: Optional[str] = Query(None, description="S3 服务状态过滤"),
    s4_status: Optional[str] = Query(None, description="S4 服务状态过滤"),
    s5_status: Optional[str] = Query(None, description="S5 服务状态过滤"),
    created_after: Optional[datetime] = Query(
        None, description="创建时间下限（ISO 8601，含）"
    ),
    created_before: Optional[datetime] = Query(
        None, description="创建时间上限（ISO 8601，含）"
    ),
    include_deleted: bool = Query(
        False, description="为 true 时包含已软删除（task_status=deleted）的记录"
    ),
    # --- 分页 ---
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数，最大 200"),
    db: AsyncSession = Depends(get_db),
) -> PlatformTaskListResponse:
    """
    多条件组合查询平台任务列表，支持分页。

    所有查询参数均为可选，不传则不过滤该字段。
    默认不返回已软删除的记录，传 include_deleted=true 可包含。
    """
    conditions = []

    if not include_deleted:
        conditions.append(PlatformTask.task_status != "deleted")

    if task_id:
        conditions.append(PlatformTask.task_id == task_id)
    if task_name:
        conditions.append(PlatformTask.task_name.contains(task_name))
    if task_status:
        conditions.append(PlatformTask.task_status == task_status)
    if ingest_id is not None:
        conditions.append(PlatformTask.ingest_id == ingest_id)
    if s1_status:
        conditions.append(PlatformTask.s1_status == s1_status)
    if s2_status:
        conditions.append(PlatformTask.s2_status == s2_status)
    if s3_status:
        conditions.append(PlatformTask.s3_status == s3_status)
    if s4_status:
        conditions.append(PlatformTask.s4_status == s4_status)
    if s5_status:
        conditions.append(PlatformTask.s5_status == s5_status)
    if created_after:
        conditions.append(PlatformTask.created_at >= created_after)
    if created_before:
        conditions.append(PlatformTask.created_at <= created_before)

    where_clause = and_(*conditions) if conditions else True

    # 总数
    count_result = await db.execute(
        select(func.count()).select_from(PlatformTask).where(where_clause)
    )
    total = count_result.scalar_one()

    # 分页数据，按创建时间倒序
    offset = (page - 1) * page_size
    data_result = await db.execute(
        select(PlatformTask)
        .where(where_clause)
        .order_by(PlatformTask.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = data_result.scalars().all()

    logger.info(
        "query_platform_tasks — total=%d page=%d page_size=%d conditions_count=%d",
        total, page, page_size, len(conditions),
    )

    return PlatformTaskListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[PlatformTaskResponse.model_validate(r) for r in rows],
    )


# ===========================================================================
# GET /platform/tasks/{task_id}  — 查询单条任务
# ===========================================================================

@router.get(
    "/platform/tasks/{task_id}",
    response_model=PlatformTaskResponse,
    summary="根据 task_id 查询单条平台任务",
    tags=["platform-tasks"],
)
async def get_platform_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlatformTaskResponse:
    result = await db.execute(
        select(PlatformTask).where(PlatformTask.task_id == task_id)
    )
    pt = result.scalar_one_or_none()
    if pt is None:
        raise HTTPException(status_code=404, detail=f"task_id={task_id!r} not found")
    return PlatformTaskResponse.model_validate(pt)


# ===========================================================================
# PATCH /platform/tasks/{task_id}/service-status  — 服务回调更新状态
# ===========================================================================

@router.patch(
    "/platform/tasks/{task_id}/service-status",
    response_model=ServiceStatusUpdateResponse,
    summary="扫描服务回调：更新对应服务状态并自动推导任务整体状态",
    tags=["platform-tasks"],
)
async def update_service_status(
    task_id: str,
    body: ServiceStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> ServiceStatusUpdateResponse:
    """
    供后端五个扫描服务（S1~S5）在完成/失败时调用。

    - 更新 `s{N}_status` 为请求中的 `status`
    - 自动重新推导 `task_status`（active / completed / failed）
    - 已软删除的任务拒绝更新（返回 409）
    """
    result = await db.execute(
        select(PlatformTask).where(PlatformTask.task_id == task_id)
    )
    pt = result.scalar_one_or_none()
    if pt is None:
        raise HTTPException(status_code=404, detail=f"task_id={task_id!r} not found")

    if pt.task_status == "deleted":
        raise HTTPException(
            status_code=409,
            detail=f"task_id={task_id!r} has been deleted, cannot update service status",
        )

    service_field = f"{body.service.lower()}_status"  # e.g. s3_status
    old_svc_status = getattr(pt, service_field)
    setattr(pt, service_field, body.status)

    # 推导新的任务整体状态
    new_task_status = derive_task_status(pt)
    pt.task_status = new_task_status
    pt.updated_at = datetime.now(timezone.utc)

    await db.flush()

    logger.info(
        "update_service_status — task_id=%s service=%s %s→%s task_status=%s message=%s",
        task_id,
        body.service,
        old_svc_status,
        body.status,
        new_task_status,
        body.message or "",
    )

    return ServiceStatusUpdateResponse(
        task_id=task_id,
        service=body.service,
        service_status=body.status,
        task_status=new_task_status,
        updated_at=pt.updated_at,
    )
