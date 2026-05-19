"""
OAT 规则配置管理接口。

  GET    /platform/oat-rules                        列出所有规则配置
  POST   /platform/oat-rules                        创建规则配置
  GET    /platform/oat-rules/builtin-xml            查看 oat_python 内置默认 XML（只读参考）
  GET    /platform/oat-rules/{rule_id}              获取单条规则配置
  PUT    /platform/oat-rules/{rule_id}              全量更新规则配置
  DELETE /platform/oat-rules/{rule_id}              删除规则配置

  GET    /platform/oat-scan-results/{task_id}       查询某平台任务的 S1 扫描结果
  DELETE /platform/tasks/{task_id}/s1               取消/删除 S1 扫描（撤销 Celery 任务并标记失败）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.database import get_db
from app.models.oat_rule_config import OatRuleConfig
from app.models.oat_scan_result import OatScanResult
from app.models.platform_task import PlatformTask, derive_task_status
from app.schemas.oat_rule import (
    BuiltinXmlResponse,
    OatRuleConfigCreate,
    OatRuleConfigListResponse,
    OatRuleConfigResponse,
    OatRuleConfigUpdate,
    OatScanResultListResponse,
    OatScanResultResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["oat-rules"])

# oat_python 内置默认 XML 路径
_BUILTIN_DEFAULT_XML = (
    Path(__file__).parent.parent
    / "tools" / "oat_python" / "src" / "oat" / "resources" / "OAT-Default.xml"
)
_BUILTIN_COMMON_XML = (
    Path(__file__).parent.parent
    / "tools" / "oat_python" / "src" / "oat" / "resources" / "OAT-Common.xml"
)


# ===========================================================================
# GET /platform/oat-rules  — 列出所有规则配置
# ===========================================================================

@router.get(
    "/platform/oat-rules",
    response_model=OatRuleConfigListResponse,
    summary="列出所有 OAT 规则配置",
    tags=["oat-rules"],
)
async def list_oat_rules(
    is_active: Optional[bool] = Query(None, description="按启用状态过滤；不传则返回全部"),
    db: AsyncSession = Depends(get_db),
) -> OatRuleConfigListResponse:
    stmt = select(OatRuleConfig).order_by(OatRuleConfig.created_at.desc())
    if is_active is not None:
        stmt = stmt.where(OatRuleConfig.is_active == is_active)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return OatRuleConfigListResponse(
        total=len(rows),
        items=[OatRuleConfigResponse.model_validate(r) for r in rows],
    )


# ===========================================================================
# POST /platform/oat-rules  — 创建规则配置
# ===========================================================================

@router.post(
    "/platform/oat-rules",
    response_model=OatRuleConfigResponse,
    status_code=201,
    summary="创建 OAT 规则配置",
    tags=["oat-rules"],
)
async def create_oat_rule(
    body: OatRuleConfigCreate,
    db: AsyncSession = Depends(get_db),
) -> OatRuleConfigResponse:
    existing = await db.execute(
        select(OatRuleConfig).where(OatRuleConfig.name == body.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"rule name {body.name!r} already exists")

    row = OatRuleConfig(
        name=body.name,
        description=body.description,
        xml_content=body.xml_content,
        is_active=body.is_active,
    )
    db.add(row)
    await db.flush()
    logger.info("create_oat_rule — id=%d name=%r", row.id, row.name)
    return OatRuleConfigResponse.model_validate(row)


# ===========================================================================
# GET /platform/oat-rules/builtin-xml  — 查看内置默认 XML（只读）
# ===========================================================================

@router.get(
    "/platform/oat-rules/builtin-xml",
    response_model=BuiltinXmlResponse,
    summary="查看 oat_python 内置默认规则 XML（只读参考）",
    tags=["oat-rules"],
)
async def get_builtin_xml(
    variant: str = Query("default", description="default=OAT-Default.xml；common=OAT-Common.xml"),
) -> BuiltinXmlResponse:
    """返回 oat_python 随包携带的内置 XML 内容，供前端参考编写自定义规则。不可修改。"""
    if variant == "common":
        xml_path = _BUILTIN_COMMON_XML
        filename = "OAT-Common.xml"
    else:
        xml_path = _BUILTIN_DEFAULT_XML
        filename = "OAT-Default.xml"

    if not xml_path.exists():
        raise HTTPException(status_code=404, detail=f"builtin XML not found: {filename}")

    return BuiltinXmlResponse(
        filename=filename,
        xml_content=xml_path.read_text(encoding="utf-8"),
    )


# ===========================================================================
# GET /platform/oat-rules/{rule_id}  — 获取单条
# ===========================================================================

@router.get(
    "/platform/oat-rules/{rule_id}",
    response_model=OatRuleConfigResponse,
    summary="获取单条 OAT 规则配置",
    tags=["oat-rules"],
)
async def get_oat_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> OatRuleConfigResponse:
    row = await _get_rule_or_404(rule_id, db)
    return OatRuleConfigResponse.model_validate(row)


# ===========================================================================
# PUT /platform/oat-rules/{rule_id}  — 更新规则配置
# ===========================================================================

@router.put(
    "/platform/oat-rules/{rule_id}",
    response_model=OatRuleConfigResponse,
    summary="更新 OAT 规则配置",
    tags=["oat-rules"],
)
async def update_oat_rule(
    rule_id: int,
    body: OatRuleConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> OatRuleConfigResponse:
    row = await _get_rule_or_404(rule_id, db)

    if body.name is not None and body.name != row.name:
        dup = await db.execute(
            select(OatRuleConfig).where(OatRuleConfig.name == body.name)
        )
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"rule name {body.name!r} already exists")
        row.name = body.name

    if body.description is not None:
        row.description = body.description
    if body.xml_content is not None:
        row.xml_content = body.xml_content
    if body.is_active is not None:
        row.is_active = body.is_active

    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("update_oat_rule — id=%d name=%r", row.id, row.name)
    return OatRuleConfigResponse.model_validate(row)


# ===========================================================================
# DELETE /platform/oat-rules/{rule_id}  — 删除规则配置
# ===========================================================================

@router.delete(
    "/platform/oat-rules/{rule_id}",
    status_code=204,
    summary="删除 OAT 规则配置",
    tags=["oat-rules"],
)
async def delete_oat_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    row = await _get_rule_or_404(rule_id, db)
    await db.delete(row)
    await db.flush()
    logger.info("delete_oat_rule — id=%d name=%r", rule_id, row.name)


# ===========================================================================
# GET /platform/oat-scan-results  — 列表查询（多条件筛选 + 分页）
# ===========================================================================

@router.get(
    "/platform/oat-scan-results",
    response_model=OatScanResultListResponse,
    summary="查询 OAT 扫描任务列表（多条件筛选 + 分页）",
    tags=["oat-rules"],
)
async def list_oat_scan_results(
    # ---- 分页 ----
    page: int = Query(1, ge=1, description="页码，从 1 起"),
    page_size: int = Query(20, ge=1, le=100, description="每页记录数，最大 100"),
    # ---- 精确 / 模糊匹配 ----
    platform_task_id: Optional[str] = Query(
        None, description="按平台任务 ID 筛选（模糊匹配，支持部分输入）"
    ),
    status: Optional[str] = Query(
        None, description="扫描状态筛选：running / success / failed / cancelled"
    ),
    rule_config_id: Optional[int] = Query(
        None, description="按规则配置 ID 精确筛选；传 0 表示只查使用内置默认规则的记录"
    ),
    exit_code: Optional[int] = Query(
        None, description="按 oat_python 退出码精确筛选（0=无问题，1=有issue）"
    ),
    # ---- 数值范围 ----
    min_total_issues: Optional[int] = Query(
        None, ge=0, description="issue 总数下限（含）"
    ),
    max_total_issues: Optional[int] = Query(
        None, ge=0, description="issue 总数上限（含）"
    ),
    has_issues: Optional[bool] = Query(
        None, description="true=只返回 total_issues>0；false=只返回 total_issues=0"
    ),
    # ---- 时间范围（ISO8601 字符串或 Unix 毫秒时间戳） ----
    start_date: Optional[str] = Query(
        None, description="created_at 起始时间（ISO8601 或 13 位毫秒时间戳）"
    ),
    end_date: Optional[str] = Query(
        None, description="created_at 截止时间（ISO8601 或 13 位毫秒时间戳）"
    ),
    # ---- 排序 ----
    sort_by: Optional[str] = Query(
        "created_at",
        description="排序字段：created_at / updated_at / total_issues / invalid_file_type_count / license_header_invalid_count / copyright_header_invalid_count",
    ),
    sort_order: Optional[str] = Query(
        "desc", description="排序方向：asc / desc"
    ),
    db: AsyncSession = Depends(get_db),
) -> OatScanResultListResponse:
    """
    OAT 扫描任务列表查询，支持多维度条件筛选与分页。

    **筛选条件**
    | 参数 | 说明 |
    |---|---|
    | platform_task_id | 模糊匹配平台任务 ID |
    | status | 扫描状态（running/success/failed/cancelled） |
    | rule_config_id | 规则配置 ID；传 `0` 表示内置默认规则（rule_config_id IS NULL） |
    | exit_code | oat_python 退出码 |
    | min_total_issues / max_total_issues | issue 数量范围 |
    | has_issues | true=有issue，false=无issue |
    | start_date / end_date | 创建时间范围 |

    **排序**
    - sort_by: created_at（默认）/ updated_at / total_issues / invalid_file_type_count / license_header_invalid_count / copyright_header_invalid_count
    - sort_order: desc（默认）/ asc
    """
    stmt = select(OatScanResult)

    # --- platform_task_id 模糊匹配 ---
    if platform_task_id:
        stmt = stmt.where(OatScanResult.platform_task_id.contains(platform_task_id))

    # --- status ---
    if status:
        stmt = stmt.where(OatScanResult.status == status)

    # --- rule_config_id（0 表示 NULL，即内置默认规则） ---
    if rule_config_id is not None:
        if rule_config_id == 0:
            stmt = stmt.where(OatScanResult.rule_config_id.is_(None))
        else:
            stmt = stmt.where(OatScanResult.rule_config_id == rule_config_id)

    # --- exit_code ---
    if exit_code is not None:
        stmt = stmt.where(OatScanResult.exit_code == exit_code)

    # --- issue 数量范围 ---
    if has_issues is True:
        stmt = stmt.where(OatScanResult.total_issues > 0)
    elif has_issues is False:
        stmt = stmt.where(OatScanResult.total_issues == 0)
    if min_total_issues is not None:
        stmt = stmt.where(OatScanResult.total_issues >= min_total_issues)
    if max_total_issues is not None:
        stmt = stmt.where(OatScanResult.total_issues <= max_total_issues)

    # --- 时间范围 ---
    def _parse_dt(value: str) -> datetime:
        """解析 ISO8601 字符串或 13 位毫秒时间戳。"""
        from datetime import timezone as tz
        stripped = value.strip()
        if stripped.isdigit():
            ts_sec = int(stripped) / 1000 if len(stripped) == 13 else int(stripped)
            return datetime.fromtimestamp(ts_sec, tz=tz.utc).replace(tzinfo=None)
        dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    if start_date:
        try:
            stmt = stmt.where(OatScanResult.created_at >= _parse_dt(start_date))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid start_date format: {start_date!r}")
    if end_date:
        try:
            stmt = stmt.where(OatScanResult.created_at <= _parse_dt(end_date))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid end_date format: {end_date!r}")

    # --- 排序 ---
    _sort_col_map = {
        "created_at": OatScanResult.created_at,
        "updated_at": OatScanResult.updated_at,
        "total_issues": OatScanResult.total_issues,
        "invalid_file_type_count": OatScanResult.invalid_file_type_count,
        "license_header_invalid_count": OatScanResult.license_header_invalid_count,
        "copyright_header_invalid_count": OatScanResult.copyright_header_invalid_count,
    }
    sort_col = _sort_col_map.get(sort_by or "created_at", OatScanResult.created_at)
    stmt = stmt.order_by(sort_col.asc() if sort_order == "asc" else sort_col.desc())

    # --- 统计总数 ---
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # --- 分页 ---
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    from app.schemas.oat_rule import OatScanResultListItem
    return OatScanResultListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        items=[OatScanResultListItem.model_validate(r) for r in rows],
    )


# ===========================================================================
# GET /platform/oat-scan-results/{task_id}  — 查询扫描结果
# ===========================================================================

@router.get(
    "/platform/oat-scan-results/{task_id}",
    response_model=OatScanResultResponse,
    summary="查询平台任务的 S1（OAT）扫描结果",
    tags=["oat-rules"],
)
async def get_oat_scan_result(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> OatScanResultResponse:
    """按 platform_task_id 查询最新一条 OAT 扫描结果。"""
    result = await db.execute(
        select(OatScanResult)
        .where(OatScanResult.platform_task_id == task_id)
        .order_by(OatScanResult.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No OAT scan result found for task_id={task_id!r}",
        )
    return OatScanResultResponse.model_validate(row)


# ===========================================================================
# DELETE /platform/tasks/{task_id}/s1  — 取消/删除 S1 扫描
# ===========================================================================

@router.delete(
    "/platform/tasks/{task_id}/s1",
    status_code=200,
    summary="取消/删除 S1（OAT）扫描",
    tags=["oat-rules"],
)
async def cancel_s1_scan(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    取消当前平台任务的 S1 OAT 扫描。

    行为：
    1. 查找该任务最新一条 status='running' 的 oat_scan_result 记录；
    2. 若存在 celery_task_id，立即向 Celery 发送 revoke（终止 worker 正在执行的任务）；
    3. 将 oat_scan_result.status 更新为 'cancelled'；
    4. 将 platform_task.s1_status 更新为 'failed'，并重新推导 task_status。

    若扫描已结束（非 running 状态），返回 409。
    """
    # 查找平台任务
    pt_result = await db.execute(
        select(PlatformTask).where(PlatformTask.task_id == task_id)
    )
    pt = pt_result.scalar_one_or_none()
    if pt is None:
        raise HTTPException(status_code=404, detail=f"platform_task {task_id!r} not found")

    if pt.s1_status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"S1 scan for task {task_id!r} is already in terminal state "
                f"(s1_status={pt.s1_status!r}); cannot cancel."
            ),
        )

    # 查找最新的 running oat_scan_result
    scan_res_result = await db.execute(
        select(OatScanResult)
        .where(
            OatScanResult.platform_task_id == task_id,
            OatScanResult.status == "running",
        )
        .order_by(OatScanResult.created_at.desc())
        .limit(1)
    )
    scan_row = scan_res_result.scalar_one_or_none()

    revoked = False
    celery_task_id: Optional[str] = None

    if scan_row is not None:
        celery_task_id = scan_row.celery_task_id
        # 撤销 Celery 任务（terminate=True 向 worker 发送 SIGTERM）
        if celery_task_id:
            try:
                celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
                revoked = True
                logger.info(
                    "cancel_s1_scan — celery revoke sent — task_id=%s celery_task_id=%s",
                    task_id, celery_task_id,
                )
            except Exception as exc:
                logger.warning(
                    "cancel_s1_scan — celery revoke failed (non-fatal) — task_id=%s error=%s",
                    task_id, exc,
                )
        scan_row.status = "cancelled"
        scan_row.error_message = "Cancelled by user"
        scan_row.updated_at = datetime.now(timezone.utc)

    # 更新 platform_task 状态
    pt.s1_status = "failed"
    pt.task_status = derive_task_status(pt)
    pt.updated_at = datetime.now(timezone.utc)

    await db.flush()

    logger.info(
        "cancel_s1_scan done — task_id=%s celery_task_id=%s revoked=%s",
        task_id, celery_task_id, revoked,
    )
    return {
        "status": "cancelled",
        "task_id": task_id,
        "s1_status": "failed",
        "celery_task_id": celery_task_id,
        "celery_revoked": revoked,
    }


# ===========================================================================
# 内部工具
# ===========================================================================

async def _get_rule_or_404(rule_id: int, db: AsyncSession) -> OatRuleConfig:
    result = await db.execute(
        select(OatRuleConfig).where(OatRuleConfig.id == rule_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"OAT rule id={rule_id} not found")
    return row
