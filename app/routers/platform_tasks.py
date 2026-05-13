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
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.oat_scan_result import OatScanResult
from app.models.platform_task import PlatformTask, derive_task_status
from app.schemas.platform_task import (
    AllServicesRiskStats,
    ComplianceTrendResponse,
    DashboardResponse,
    MonitorProjectStats,
    PendingRisksResponse,
    PlatformTaskListResponse,
    PlatformTaskResponse,
    RiskOverviewResponse,
    ServiceRiskStats,
    ServiceStatusUpdateRequest,
    ServiceStatusUpdateResponse,
    TrendMonthData,
    TrendServiceData,
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


# ===========================================================================
# POST /platform/tasks/{task_id}/s3/sync-status  — 实时同步 S3 扫描状态
# ===========================================================================

# sentry current_status → platform s3_status 映射
_SENTRY_STATUS_MAP: Dict[str, str] = {
    "completed": "success",
    "failed": "failed",
    "terminated": "failed",
}


@router.post(
    "/platform/tasks/{task_id}/s3/sync-status",
    summary="从 compliance-sentry 实时同步 S3 扫描状态",
    tags=["platform-tasks"],
)
async def sync_s3_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    主动从 compliance-sentry 查询最新扫描状态，并同步写入平台任务表。

    解决场景：
    - Celery worker 未启动 / 轮询任务失败，导致 `s3_status` 长期停留在 `running`
    - 前端想实时确认 sentry 是否已完成

    行为：
    1. 按 `task_id` 查找平台任务，读取 `s3_analysis_id`；
    2. 调用 `GET /analysis/{analysis_id}/status`（compliance-sentry）获取最新状态；
    3. 将 sentry 状态映射后更新 `platform_task.s3_status` 与 `task_status`；
    4. 返回同步结果。

    仅在 `s3_status` 为 `running` 或 `pending` 时执行同步；终态（`success`/`failed`/`skipped`）时直接返回当前值。
    """
    result = await db.execute(
        select(PlatformTask).where(PlatformTask.task_id == task_id)
    )
    pt = result.scalar_one_or_none()
    if pt is None:
        raise HTTPException(status_code=404, detail=f"task_id={task_id!r} not found")

    current_s3 = pt.s3_status

    # 终态或未参与 S3，直接返回，无需查 sentry
    if current_s3 in ("success", "failed", "skipped"):
        return {
            "task_id": task_id,
            "s3_status": current_s3,
            "task_status": pt.task_status,
            "s3_analysis_id": pt.s3_analysis_id,
            "synced": False,
            "reason": f"s3_status already in terminal state: {current_s3}",
        }

    analysis_id = pt.s3_analysis_id
    if not analysis_id:
        return {
            "task_id": task_id,
            "s3_status": current_s3,
            "task_status": pt.task_status,
            "s3_analysis_id": None,
            "synced": False,
            "reason": "s3_analysis_id not set yet (sentry job may not have been accepted)",
        }

    if not settings.compliance_sentry_base_url:
        raise HTTPException(status_code=503, detail="COMPLIANCE_SENTRY_BASE_URL not configured")

    # 实时查询 sentry 状态
    from app.services.sentry_auth import get_token
    try:
        token = await get_token()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sentry auth failed: {e}")

    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
    _proxy = settings.compliance_sentry_proxy or None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), proxy=_proxy) as client:
            r = await client.get(
                f"{base}/analysis/{analysis_id}/status",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"sentry request error: {exc}")

    if not r.is_success:
        raise HTTPException(
            status_code=502,
            detail=f"sentry returned HTTP {r.status_code} for analysis_id={analysis_id}",
        )

    try:
        body = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="sentry returned invalid JSON")

    data = body.get("data") or body
    sentry_status = (data.get("current_status") or "").lower()
    sentry_progress = data.get("progress", 0)

    new_s3_status = _SENTRY_STATUS_MAP.get(sentry_status)  # None 表示仍在进行中

    # 若 sentry 已 completed，顺带拉取冲突数写库
    has_conflicts: Optional[bool] = None
    conflict_count: int = 0
    if sentry_status == "completed":
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), proxy=_proxy) as client2:
                rc = await client2.get(
                    f"{base}/analysis/{analysis_id}/conflicts",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if rc.is_success:
                conflicts_body = rc.json()
                conflict_list = conflicts_body.get("conflicts", [])
                conflict_count = len(conflict_list)
                has_conflicts = conflict_count > 0
        except Exception as ce:
            logger.warning(
                "sync_s3_status — failed to fetch conflicts, skipping — task_id=%s error=%s",
                task_id, ce,
            )

    synced = False
    if new_s3_status and new_s3_status != current_s3:
        pt.s3_status = new_s3_status
        if new_s3_status == "success" and has_conflicts is not None:
            pt.s3_has_conflicts = has_conflicts
            pt.s3_conflict_count = conflict_count
        pt.task_status = derive_task_status(pt)
        pt.updated_at = datetime.now(timezone.utc)
        await db.flush()
        synced = True
        logger.info(
            "sync_s3_status — task_id=%s analysis_id=%s sentry_status=%s "
            "s3_status: %s→%s has_conflicts=%s conflict_count=%d task_status=%s",
            task_id, analysis_id, sentry_status, current_s3, new_s3_status,
            has_conflicts, conflict_count, pt.task_status,
        )
    else:
        logger.info(
            "sync_s3_status — task_id=%s analysis_id=%s sentry_status=%s (no change needed)",
            task_id, analysis_id, sentry_status,
        )

    return {
        "task_id": task_id,
        "s3_analysis_id": analysis_id,
        "sentry_status": sentry_status,
        "sentry_progress": sentry_progress,
        "s3_status": pt.s3_status,
        "s3_has_conflicts": pt.s3_has_conflicts,
        "s3_conflict_count": pt.s3_conflict_count,
        "task_status": pt.task_status,
        "synced": synced,
        "updated_at": pt.updated_at.isoformat() if synced else None,
    }


# ===========================================================================
# 看板辅助：计算月份边界
# ===========================================================================

def _month_boundaries(now: datetime) -> tuple[datetime, datetime, datetime]:
    """返回 (this_month_start, last_month_start, last_month_end)。"""
    this_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_start.month == 1:
        last_start = this_start.replace(year=this_start.year - 1, month=12)
    else:
        last_start = this_start.replace(month=this_start.month - 1)
    return this_start, last_start, this_start  # last_month_end == this_month_start


def _make_stats(current: int, last_month: int) -> ServiceRiskStats:
    change = current - last_month
    change_rate = round(change / last_month * 100, 2) if last_month != 0 else None
    return ServiceRiskStats(
        current=current, last_month=last_month,
        change=change, change_rate=change_rate,
        integrated=True,
    )


_PLACEHOLDER = ServiceRiskStats(current=0, last_month=0, change=0, change_rate=None, integrated=False)


# ===========================================================================
# GET /platform/dashboard/risk-overview  — 总体风险数看板
# ===========================================================================

@router.get(
    "/platform/dashboard/risk-overview",
    response_model=RiskOverviewResponse,
    summary="首页看板：总体风险数（各服务 + 汇总，含环比）",
    tags=["platform-dashboard"],
)
async def dashboard_risk_overview(
    db: AsyncSession = Depends(get_db),
) -> RiskOverviewResponse:
    """
    统计本月与上月各子服务检测到风险的任务数，并计算环比涨跌。

    **风险定义**：
    - **S1 (OAT)**：`oat_scan_result.status = 'success'` 且 `total_issues > 0`
    - **S3 (sentry)**：`platform_task.s3_status = 'success'` 且 `s3_has_conflicts = true`
    - **S2/S4/S5**：预留，当前统计为 0

    统计口径：按 `platform_task.created_at` 所在自然月分组。
    """
    now = datetime.now(timezone.utc)
    this_start, last_start, last_end = _month_boundaries(now)

    async def _s1_risks(start: datetime, end: datetime) -> int:
        r = await db.execute(
            select(func.count(OatScanResult.id))
            .join(PlatformTask, PlatformTask.task_id == OatScanResult.platform_task_id)
            .where(and_(
                OatScanResult.status == "success",
                OatScanResult.total_issues > 0,
                PlatformTask.created_at >= start,
                PlatformTask.created_at < end,
            ))
        )
        return r.scalar_one()

    async def _s3_risks(start: datetime, end: datetime) -> int:
        r = await db.execute(
            select(func.count())
            .select_from(PlatformTask)
            .where(and_(
                PlatformTask.s3_status == "success",
                PlatformTask.s3_has_conflicts.is_(True),
                PlatformTask.created_at >= start,
                PlatformTask.created_at < end,
            ))
        )
        return r.scalar_one()

    s1_cur, s1_last = await _s1_risks(this_start, now), await _s1_risks(last_start, last_end)
    s3_cur, s3_last = await _s3_risks(this_start, now), await _s3_risks(last_start, last_end)

    total_cur = s1_cur + s3_cur
    total_last = s1_last + s3_last

    logger.info(
        "dashboard_risk_overview — month=%s s1=%d/%d s3=%d/%d total=%d/%d",
        now.strftime("%Y-%m"), s1_cur, s1_last, s3_cur, s3_last, total_cur, total_last,
    )

    return RiskOverviewResponse(
        month=now.strftime("%Y-%m"),
        total=_make_stats(total_cur, total_last),
        by_service=AllServicesRiskStats(
            s1=_make_stats(s1_cur, s1_last),
            s2=_PLACEHOLDER,
            s3=_make_stats(s3_cur, s3_last),
            s4=_PLACEHOLDER,
            s5=_PLACEHOLDER,
        ),
    )


# ===========================================================================
# GET /platform/dashboard/pending-risks  — 待处理（进行中）任务看板
# ===========================================================================

@router.get(
    "/platform/dashboard/pending-risks",
    response_model=PendingRisksResponse,
    summary="首页看板：待处理扫描任务数（各服务 + 汇总，含环比）",
    tags=["platform-dashboard"],
)
async def dashboard_pending_risks(
    db: AsyncSession = Depends(get_db),
) -> PendingRisksResponse:
    """
    统计本月与上月各子服务处于 `pending` 或 `running` 状态的任务数（尚未得出结果），并计算环比涨跌。

    反映当前扫描队列的繁忙程度：数值越高说明积压越多。

    统计口径：按 `platform_task.created_at` 所在自然月分组。
    """
    now = datetime.now(timezone.utc)
    this_start, last_start, last_end = _month_boundaries(now)
    _PENDING_STATES = ("pending", "running")

    async def _s1_pending(start: datetime, end: datetime) -> int:
        r = await db.execute(
            select(func.count())
            .select_from(PlatformTask)
            .where(and_(
                PlatformTask.s1_status.in_(_PENDING_STATES),
                PlatformTask.created_at >= start,
                PlatformTask.created_at < end,
            ))
        )
        return r.scalar_one()

    async def _s3_pending(start: datetime, end: datetime) -> int:
        r = await db.execute(
            select(func.count())
            .select_from(PlatformTask)
            .where(and_(
                PlatformTask.s3_status.in_(_PENDING_STATES),
                PlatformTask.created_at >= start,
                PlatformTask.created_at < end,
            ))
        )
        return r.scalar_one()

    s1_cur, s1_last = await _s1_pending(this_start, now), await _s1_pending(last_start, last_end)
    s3_cur, s3_last = await _s3_pending(this_start, now), await _s3_pending(last_start, last_end)

    total_cur = s1_cur + s3_cur
    total_last = s1_last + s3_last

    logger.info(
        "dashboard_pending_risks — month=%s s1=%d/%d s3=%d/%d total=%d/%d",
        now.strftime("%Y-%m"), s1_cur, s1_last, s3_cur, s3_last, total_cur, total_last,
    )

    return PendingRisksResponse(
        month=now.strftime("%Y-%m"),
        total=_make_stats(total_cur, total_last),
        by_service=AllServicesRiskStats(
            s1=_make_stats(s1_cur, s1_last),
            s2=_PLACEHOLDER,
            s3=_make_stats(s3_cur, s3_last),
            s4=_PLACEHOLDER,
            s5=_PLACEHOLDER,
        ),
    )


# ===========================================================================
# GET /platform/dashboard/compliance-trend  — 最近 6 个月合规趋势
# ===========================================================================

@router.get(
    "/platform/dashboard/compliance-trend",
    response_model=ComplianceTrendResponse,
    summary="首页看板：最近 6 个月合规趋势",
    tags=["platform-dashboard"],
)
async def dashboard_compliance_trend(
    db: AsyncSession = Depends(get_db),
) -> ComplianceTrendResponse:
    """
    返回最近 6 个自然月（含当前月份）每月的扫描总量与风险占比。

    **各月数据定义**：
    - `total_scans`：当月所有服务合计完成扫描数（success + failed）
    - `risk_count` ：其中检测到风险的任务数
    - `risk_rate`  ：风险占比（%）

    **S1 (OAT)**：完成扫描 = s1_status IN ('success','failed')；风险 = total_issues > 0 AND status='success'
    **S3 (sentry)**：完成扫描 = s3_status IN ('success','failed')；风险 = s3_status='success' AND s3_has_conflicts=true
    **S2/S4/S5**：预留，当前恒为 0
    """
    now = datetime.now(timezone.utc)

    # 计算最近 6 个月的起始时间
    months: List[tuple[str, datetime, datetime]] = []  # (label, start, end)
    cur = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(6):
        label = cur.strftime("%Y-%m")
        # 月末 = 下个月的第一天
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        months.append((label, cur, nxt))
        # 上个月
        if cur.month == 1:
            cur = cur.replace(year=cur.year - 1, month=12)
        else:
            cur = cur.replace(month=cur.month - 1)
    months.reverse()  # 从最早到最近

    six_months_ago = months[0][1]

    # S1：按月分组统计扫描数和风险数（JOIN oat_scan_result）
    s1_rows = (await db.execute(
        select(
            func.date_format(PlatformTask.created_at, "%Y-%m").label("ym"),
            func.count(OatScanResult.id).label("scans"),
            func.sum(
                case((and_(OatScanResult.status == "success", OatScanResult.total_issues > 0), 1), else_=0)
            ).label("risks"),
        )
        .join(OatScanResult, OatScanResult.platform_task_id == PlatformTask.task_id)
        .where(PlatformTask.created_at >= six_months_ago)
        .group_by(func.date_format(PlatformTask.created_at, "%Y-%m"))
    )).all()
    s1_map: Dict[str, Dict[str, int]] = {
        row.ym: {"scans": int(row.scans), "risks": int(row.risks)}
        for row in s1_rows
    }

    # S3：按月分组统计扫描数和风险数（直接用 platform_task 字段）
    s3_rows = (await db.execute(
        select(
            func.date_format(PlatformTask.created_at, "%Y-%m").label("ym"),
            func.sum(
                case((PlatformTask.s3_status.in_(["success", "failed"]), 1), else_=0)
            ).label("scans"),
            func.sum(
                case((and_(PlatformTask.s3_status == "success", PlatformTask.s3_has_conflicts.is_(True)), 1), else_=0)
            ).label("risks"),
        )
        .select_from(PlatformTask)
        .where(and_(
            PlatformTask.created_at >= six_months_ago,
            PlatformTask.s3_status != "skipped",
        ))
        .group_by(func.date_format(PlatformTask.created_at, "%Y-%m"))
    )).all()
    s3_map: Dict[str, Dict[str, int]] = {
        row.ym: {"scans": int(row.scans), "risks": int(row.risks)}
        for row in s3_rows
    }

    # 组装结果
    result_months: List[TrendMonthData] = []
    for label, _start, _end in months:
        s1d = s1_map.get(label, {"scans": 0, "risks": 0})
        s3d = s3_map.get(label, {"scans": 0, "risks": 0})
        total_scans = s1d["scans"] + s3d["scans"]
        risk_count = s1d["risks"] + s3d["risks"]
        risk_rate = round(risk_count / total_scans * 100, 2) if total_scans > 0 else None
        result_months.append(TrendMonthData(
            month=label,
            total_scans=total_scans,
            risk_count=risk_count,
            risk_rate=risk_rate,
            by_service={
                "s1": TrendServiceData(scans=s1d["scans"], risks=s1d["risks"]),
                "s2": TrendServiceData(scans=0, risks=0),
                "s3": TrendServiceData(scans=s3d["scans"], risks=s3d["risks"]),
                "s4": TrendServiceData(scans=0, risks=0),
                "s5": TrendServiceData(scans=0, risks=0),
            },
        ))

    logger.info(
        "dashboard_compliance_trend — last 6 months summary: %s",
        [(m.month, m.total_scans, m.risk_count) for m in result_months],
    )
    return ComplianceTrendResponse(months=result_months)
