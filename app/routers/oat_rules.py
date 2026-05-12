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
from sqlalchemy import select
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
