"""
compliance-sentry-main 全量接口对接路由。

代理架构：所有接口均通过 proxy_to_sentry 透传到 compliance-sentry-main 后端，
网关本身不持有 sentry 的业务逻辑，仅做请求转发与 Authorization 头传递。

接口分组（与 sentry api/v1/api.py 保持一致）：
  - /platform/compliance-sentry/v1/auth/...         认证 (auth.py)
  - /platform/compliance-sentry/v1/users/...        用户管理 (users.py)
  - /platform/compliance-sentry/v1/analysis/...     分析任务 (analysis.py)
  - /platform/compliance-sentry/v1/mission/...      任务提交 (analysis.py)
  - /platform/compliance-sentry/v1/analyze/...      客户端分析 (analysis.py)
  - /platform/compliance-sentry/v1/kb/...           知识库 (kb.py)
  - /platform/compliance-sentry/v1/dashboard/...    仪表盘 (dashboard.py)
  - /platform/compliance-sentry/v1/system/...       系统 (system.py)
  - /platform/compliance-sentry/v1/conflicts/...    冲突搜索 (conflicts.py)
  - /platform/compliance-sentry/v1/tasks/...        任务资产 (task_assets.py)

平台专属接口：
  - POST /platform/tasks                            平台任务总入口（文件入库 + 可选触发 sentry）
  - GET  /platform/tasks/result/{task_id}           查询 Celery 异步任务结果
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_db
from app.models.file_ingest import FileIngestResult
from app.models.oat_scan_result import OatScanResult
from app.models.platform_task import PlatformTask, derive_task_status
from app.services.file_ingest import ingest_from_upload, ingest_from_url
from app.services.oat_scanner import oat_scan_task, run_oat_scan
from app.services.platform_tasks import sentry_mission_task
from app.services.sentry_auth import get_token
from app.services.sentry_proxy import proxy_to_sentry, proxy_to_sentry_noauth

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compliance-sentry", "platform"])

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _sentry_base() -> str:
    """返回 sentry 基础 URL，未配置时抛 503。"""
    if not settings.compliance_sentry_base_url:
        raise HTTPException(status_code=503, detail="COMPLIANCE_SENTRY_BASE_URL not configured")
    return settings.compliance_sentry_base_url


async def _update_platform_task_s3(
    db: AsyncSession,
    platform_task_id: str,
    s3_status: str,
    analysis_id: Optional[str] = None,
) -> None:
    """
    按 platform_task_id 查找主任务，更新 s3_status 并重新推导 task_status。
    若传入 analysis_id，同时写入 platform_task.s3_analysis_id。
    找不到记录时仅打日志，不抛异常（避免阻断主流程）。
    """
    from datetime import datetime, timezone
    from sqlalchemy import select as _select

    try:
        result = await db.execute(
            _select(PlatformTask).where(PlatformTask.task_id == platform_task_id)
        )
        pt = result.scalar_one_or_none()
        if pt is None:
            logger.warning(
                "_update_platform_task_s3 — platform_task_id=%s not found, skip",
                platform_task_id,
            )
            return
        pt.s3_status = s3_status
        if analysis_id:
            pt.s3_analysis_id = analysis_id
        pt.task_status = derive_task_status(pt)
        pt.updated_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info(
            "_update_platform_task_s3 — platform_task_id=%s s3_status=%s analysis_id=%s task_status=%s",
            platform_task_id, s3_status, analysis_id or "N/A", pt.task_status,
        )
    except Exception as exc:
        logger.error(
            "_update_platform_task_s3 — failed to update platform_task_id=%s: %s",
            platform_task_id, exc,
        )


# ===========================================================================
# 认证模块  /auth  (sentry: auth.py)
# ===========================================================================

@router.post(
    "/platform/compliance-sentry/v1/auth/register",
    summary="[sentry] 注册新用户",
    tags=["sentry-auth"],
)
async def sentry_auth_register(request: Request):
    return await proxy_to_sentry(_sentry_base(), "auth/register", request)


@router.post(
    "/platform/compliance-sentry/v1/auth/login",
    summary="[sentry] 用户登录，返回 JWT Token",
    tags=["sentry-auth"],
)
async def sentry_auth_login(request: Request):
    return await proxy_to_sentry(_sentry_base(), "auth/login", request)


@router.put(
    "/platform/compliance-sentry/v1/auth/change-password",
    summary="[sentry] 修改当前用户密码",
    tags=["sentry-auth"],
)
async def sentry_auth_change_password(request: Request):
    return await proxy_to_sentry(_sentry_base(), "auth/change-password", request)


@router.put(
    "/platform/compliance-sentry/v1/auth/admin/change-password",
    summary="[sentry] 管理员修改任意用户密码",
    tags=["sentry-auth"],
)
async def sentry_auth_admin_change_password(request: Request):
    return await proxy_to_sentry(_sentry_base(), "auth/admin/change-password", request)


# ===========================================================================
# 用户管理模块  /users  (sentry: users.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/users/me",
    summary="[sentry] 获取当前用户信息",
    tags=["sentry-users"],
)
async def sentry_users_me(request: Request):
    return await proxy_to_sentry(_sentry_base(), "users/me", request)


@router.get(
    "/platform/compliance-sentry/v1/users/all",
    summary="[sentry] 获取用户列表（管理员）",
    tags=["sentry-users"],
)
async def sentry_users_all(request: Request):
    return await proxy_to_sentry(_sentry_base(), "users/all", request)


@router.put(
    "/platform/compliance-sentry/v1/users/{user_id}",
    summary="[sentry] 更新用户信息",
    tags=["sentry-users"],
)
async def sentry_users_update(user_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"users/{user_id}", request)


@router.delete(
    "/platform/compliance-sentry/v1/users/{user_id}",
    summary="[sentry] 删除用户（管理员）",
    tags=["sentry-users"],
)
async def sentry_users_delete(user_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"users/{user_id}", request)


# ===========================================================================
# 分析任务模块  /analysis  /mission  /analyze  (sentry: analysis.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/analysis/tasks",
    summary="[sentry] 获取分析任务列表（支持分页与筛选）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_tasks(request: Request):
    return await proxy_to_sentry(_sentry_base(), "analysis/tasks", request)


@router.post(
    "/platform/compliance-sentry/v1/mission",
    summary="[sentry] 提交系统任务（管理员，支持 shadow 文件）",
    tags=["sentry-analysis"],
)
async def sentry_mission_submit(request: Request):
    return await proxy_to_sentry(_sentry_base(), "mission", request)


@router.post(
    "/platform/compliance-sentry/v1/mission/upload",
    summary="[sentry] 上传 ZIP 并提交应用级检测任务",
    tags=["sentry-analysis"],
)
async def sentry_mission_upload(request: Request):
    return await proxy_to_sentry(_sentry_base(), "mission/upload", request)


@router.post(
    "/platform/compliance-sentry/v1/mission/git",
    summary="[sentry] 通过 Git URL 提交应用级检测任务",
    tags=["sentry-analysis"],
)
async def sentry_mission_git(request: Request):
    return await proxy_to_sentry(_sentry_base(), "mission/git", request)


@router.post(
    "/platform/compliance-sentry/v1/analyze/client",
    summary="[sentry] 提交预处理数据启动分析",
    tags=["sentry-analysis"],
)
async def sentry_analyze_client(request: Request):
    return await proxy_to_sentry(_sentry_base(), "analyze/client", request)


@router.delete(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}",
    summary="[sentry] 删除分析任务",
    tags=["sentry-analysis"],
)
async def sentry_analysis_delete(
    analysis_id: str,
    request: Request,
    platform_task_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    删除 sentry 分析任务并透传响应。
    若提供 platform_task_id，删除成功后将主任务的 s3_status 更新为 failed，
    task_status 同步重新推导（表示该扫描服务已被中止/删除）。
    """
    resp = await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}", request)
    if platform_task_id:
        # 判断转发是否成功（2xx）——proxy_to_sentry 返回 Response 对象
        status_code = getattr(resp, "status_code", 200)
        if status_code < 300:
            await _update_platform_task_s3(db, platform_task_id, "failed")
            logger.info(
                "sentry_analysis_delete — analysis_id=%s platform_task_id=%s s3→failed",
                analysis_id, platform_task_id,
            )
    return resp


@router.post(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/terminate",
    summary="[sentry] 终止分析任务",
    tags=["sentry-analysis"],
)
async def sentry_analysis_terminate(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/terminate", request)


@router.post(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/retry",
    summary="[sentry] 重试分析任务（terminated/failed 状态）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_retry(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/retry", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/status",
    summary="[sentry] 获取分析任务状态与进度",
    tags=["sentry-analysis"],
)
async def sentry_analysis_status(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/status", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/parameters",
    summary="[sentry] 获取任务参数与源码缓存状态",
    tags=["sentry-analysis"],
)
async def sentry_analysis_parameters(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/parameters", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/report",
    summary="[sentry] 获取分析报告（JSON/PDF/HTML）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_report(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/report", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/report/{report_type}",
    summary="[sentry] 下载分析报告文件（dependency_graph/license_map/compatible_graph/final_result）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_report_download(analysis_id: str, report_type: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/report/{report_type}", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependencies",
    summary="[sentry] 获取依赖关系图（数据库版）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_dependencies(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/dependencies", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph",
    summary="[sentry] 获取依赖图节点与边（GML/JSON 文件版）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_dependency_graph(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/dependency-graph", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/skeleton",
    summary="[sentry] 获取依赖图骨架结构",
    tags=["sentry-analysis"],
)
async def sentry_analysis_dependency_graph_skeleton(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/dependency-graph/skeleton", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/node-metadata",
    summary="[sentry] 获取依赖图节点属性",
    tags=["sentry-analysis"],
)
async def sentry_analysis_dependency_graph_node_metadata(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/dependency-graph/node-metadata", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/edge-metadata",
    summary="[sentry] 获取依赖图边属性",
    tags=["sentry-analysis"],
)
async def sentry_analysis_dependency_graph_edge_metadata(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/dependency-graph/edge-metadata", request)


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/dependency-graph/compatibility-results",
    summary="[sentry] 获取压缩的兼容性检查结果",
    tags=["sentry-analysis"],
)
async def sentry_analysis_compatibility_results(analysis_id: str, request: Request):
    return await proxy_to_sentry(
        _sentry_base(), f"analysis/{analysis_id}/dependency-graph/compatibility-results", request
    )


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/intermediate/license-map",
    summary="[sentry] 获取中间产物（许可证映射/ScanCode 结果）",
    tags=["sentry-analysis"],
)
async def sentry_analysis_intermediate_license_map(analysis_id: str, request: Request):
    return await proxy_to_sentry(
        _sentry_base(), f"analysis/{analysis_id}/intermediate/license-map", request
    )


@router.get(
    "/platform/compliance-sentry/v1/analysis/{analysis_id}/conflicts",
    summary="[sentry] 获取许可证冲突",
    tags=["sentry-analysis"],
)
async def sentry_analysis_conflicts(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}/conflicts", request)


@router.get(
    "/platform/compliance-sentry/v1/mission/{mission_id}/metrics/latest",
    summary="[sentry] 获取任务资源监控最新数据（CPU/内存）",
    tags=["sentry-analysis"],
)
async def sentry_mission_metrics_latest(mission_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"mission/{mission_id}/metrics/latest", request)


# ===========================================================================
# 知识库模块  /kb  (sentry: kb.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/kb/licenses",
    summary="[sentry] 获取所有许可证列表（仅名称）",
    tags=["sentry-kb"],
)
async def sentry_kb_licenses(request: Request):
    return await proxy_to_sentry(_sentry_base(), "kb/licenses", request)


@router.get(
    "/platform/compliance-sentry/v1/kb/licenses/{spdx_id}",
    summary="[sentry] 获取特定许可证详情",
    tags=["sentry-kb"],
)
async def sentry_kb_license_detail(spdx_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"kb/licenses/{spdx_id}", request)


@router.put(
    "/platform/compliance-sentry/v1/kb/licenses/{spdx_id}",
    summary="[sentry] 修改许可证信息",
    tags=["sentry-kb"],
)
async def sentry_kb_license_update(spdx_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"kb/licenses/{spdx_id}", request)


@router.delete(
    "/platform/compliance-sentry/v1/kb/licenses/{spdx_id}",
    summary="[sentry] 删除许可证（软删除，仅管理员）",
    tags=["sentry-kb"],
)
async def sentry_kb_license_delete(spdx_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"kb/licenses/{spdx_id}", request)


@router.post(
    "/platform/compliance-sentry/v1/kb/licenses/compatibility",
    summary="[sentry] 检查许可证兼容性",
    tags=["sentry-kb"],
)
async def sentry_kb_licenses_compatibility(request: Request):
    return await proxy_to_sentry(_sentry_base(), "kb/licenses/compatibility", request)


@router.post(
    "/platform/compliance-sentry/v1/kb/licenses/upload",
    summary="[sentry] 批量上传许可证文件",
    tags=["sentry-kb"],
)
async def sentry_kb_licenses_upload(request: Request):
    return await proxy_to_sentry(_sentry_base(), "kb/licenses/upload", request)


@router.get(
    "/platform/compliance-sentry/v1/kb/compatibility/{license_a}/{license_b}",
    summary="[sentry] 查询两个许可证的兼容性",
    tags=["sentry-kb"],
)
async def sentry_kb_compatibility_pair(license_a: str, license_b: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"kb/compatibility/{license_a}/{license_b}", request)


@router.get(
    "/platform/compliance-sentry/v1/kb/compatibility/{license_id}/all",
    summary="[sentry] 获取许可证的所有兼容性关系",
    tags=["sentry-kb"],
)
async def sentry_kb_compatibility_all(license_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"kb/compatibility/{license_id}/all", request)


@router.get(
    "/platform/compliance-sentry/v1/kb/compatibility/matrix",
    summary="[sentry] 获取完整的兼容性矩阵",
    tags=["sentry-kb"],
)
async def sentry_kb_compatibility_matrix(request: Request):
    return await proxy_to_sentry(_sentry_base(), "kb/compatibility/matrix", request)


@router.post(
    "/platform/compliance-sentry/v1/kb/admin/initialize",
    summary="[sentry] 初始化知识库（管理员）",
    tags=["sentry-kb"],
)
async def sentry_kb_admin_initialize(request: Request):
    return await proxy_to_sentry(_sentry_base(), "kb/admin/initialize", request)


# ===========================================================================
# 仪表盘模块  /dashboard  (sentry: dashboard.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/dashboard/overview",
    summary="[sentry] 获取系统总览",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_overview(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/overview", request)


@router.get(
    "/platform/compliance-sentry/v1/dashboard/task-stats",
    summary="[sentry] 获取最近 7 天任务统计",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_task_stats(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/task-stats", request)


@router.get(
    "/platform/compliance-sentry/v1/dashboard/license-distribution",
    summary="[sentry] 获取许可证分布统计",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_license_distribution(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/license-distribution", request)


@router.get(
    "/platform/compliance-sentry/v1/dashboard/system-resources",
    summary="[sentry] 获取当前系统资源使用率（CPU/内存）",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_system_resources(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/system-resources", request)


@router.get(
    "/platform/compliance-sentry/v1/dashboard/task-status-distribution",
    summary="[sentry] 获取任务状态分布",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_task_status_distribution(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/task-status-distribution", request)


@router.get(
    "/platform/compliance-sentry/v1/dashboard/daily-summary",
    summary="[sentry] 获取当天任务统计",
    tags=["sentry-dashboard"],
)
async def sentry_dashboard_daily_summary(request: Request):
    return await proxy_to_sentry(_sentry_base(), "dashboard/daily-summary", request)


# ===========================================================================
# 系统模块  /system  (sentry: system.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/system/health",
    summary="[sentry] 获取系统健康状态",
    tags=["sentry-system"],
)
async def sentry_system_health(request: Request):
    return await proxy_to_sentry(_sentry_base(), "system/health", request)


@router.get(
    "/platform/compliance-sentry/v1/system/task-limits",
    summary="[sentry] 获取任务并发限制配置（管理员）",
    tags=["sentry-system"],
)
async def sentry_system_task_limits_get(request: Request):
    return await proxy_to_sentry(_sentry_base(), "system/task-limits", request)


@router.put(
    "/platform/compliance-sentry/v1/system/task-limits",
    summary="[sentry] 更新任务并发限制配置（管理员）",
    tags=["sentry-system"],
)
async def sentry_system_task_limits_update(request: Request):
    return await proxy_to_sentry(_sentry_base(), "system/task-limits", request)


# ===========================================================================
# 冲突搜索模块  /conflicts  (sentry: conflicts.py)
# ===========================================================================

@router.get(
    "/platform/compliance-sentry/v1/conflicts/search",
    summary="[sentry] 搜索许可证冲突",
    tags=["sentry-conflicts"],
)
async def sentry_conflicts_search(request: Request):
    return await proxy_to_sentry(_sentry_base(), "conflicts/search", request)


# ===========================================================================
# 任务资产模块  /tasks  (sentry: task_assets.py)
# ===========================================================================

@router.post(
    "/platform/compliance-sentry/v1/tasks/{task_id}/files",
    summary="[sentry] 上传/替换 shadow_file 与 license_shadow",
    tags=["sentry-task-assets"],
)
async def sentry_task_files_upload(task_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/files", request)


@router.get(
    "/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/file_shadow",
    summary="[sentry] 获取 file_shadow 的 Base64",
    tags=["sentry-task-assets"],
)
async def sentry_task_files_base64_file_shadow(task_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/files/base64/file_shadow", request)


@router.get(
    "/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/license_shadow",
    summary="[sentry] 获取 license_shadow 的 Base64",
    tags=["sentry-task-assets"],
)
async def sentry_task_files_base64_license_shadow(task_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/files/base64/license_shadow", request)


@router.get(
    "/platform/compliance-sentry/v1/tasks/{task_id}/files/base64/config",
    summary="[sentry] 获取 config 的 Base64",
    tags=["sentry-task-assets"],
)
async def sentry_task_files_base64_config(task_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/files/base64/config", request)


@router.get(
    "/platform/compliance-sentry/v1/tasks/{task_id}/keys",
    summary="[sentry] 获取任务键数组（若为空将尝试从检测结果加载）",
    tags=["sentry-task-assets"],
)
async def sentry_task_keys_get(task_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/keys", request)


@router.delete(
    "/platform/compliance-sentry/v1/tasks/{task_id}/keys/{key}",
    summary="[sentry] 删除任务键数组中的一个键",
    tags=["sentry-task-assets"],
)
async def sentry_task_keys_delete(task_id: str, key: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"tasks/{task_id}/keys/{key}", request)


# ===========================================================================
# 兜底通配符代理（覆盖未显式列出的路径，如 sentry 新增接口）
# ===========================================================================

@router.api_route(
    "/platform/compliance-sentry/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    summary="[sentry] 通用透传（兜底）",
    include_in_schema=False,
)
async def compliance_sentry_proxy_fallback(path: str, request: Request):
    """
    兜底代理：当 sentry 新增接口而网关尚未显式注册时，仍可透传。
    显式路由优先级高于此兜底路由。
    """
    return await proxy_to_sentry(_sentry_base(), path, request)


# ===========================================================================
# 平台专属接口
# ===========================================================================

@router.post(
    "/platform/tasks",
    summary="平台任务总入口（文件入库 + 可选触发 sentry/OAT 扫描）",
    tags=["platform"],
)
async def platform_tasks(
    task_name: str = Form(..., description="任务名称（提交 sentry 必填）"),
    services: List[str] = Form(
        ...,
        description="扫描服务多选：S1/S2/S3/S4；S1=OAT合规扫描，S3=compliance-sentry",
    ),
    async_scan: bool = Form(False, description="为 true 时各服务提交走 Celery，立即返回 task_id"),
    source_url: Optional[str] = Form(None, description="Git 仓库地址（与 file 二选一）"),
    file: Optional[UploadFile] = File(None, description="上传 zip 等（与 source_url 二选一）"),
    third_party: bool = Form(False),
    fallback_tree: bool = Form(False),
    branch_tag: Optional[str] = Form(None, description="git 任务可选分支"),
    shadow_file: Optional[UploadFile] = File(None, description="compliance-sentry mission 的 shadow_file"),
    license_shadow: Optional[UploadFile] = File(None, description="compliance-sentry mission 的 license_shadow"),
    s1_rule_config_id: Optional[int] = Form(
        None,
        description=(
            "S1（OAT）扫描使用的规则配置 ID（来自 /platform/oat-rules）。"
            "不传则使用 oat_python 内置默认规则。"
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    平台任务总入口（支持异步）：
    1. 根据 file 或 source_url 拉取代码并解析目录树，写入 MySQL（ingest_id）；
    2. 若 services 包含 `S1`（OAT）：对源码执行开源合规扫描，支持自定义规则；
    3. 若 services 包含 `S3`（compliance-sentry）：再向 sentry 提交 mission/upload（zip）或 mission/git（仓库地址）。
    """
    services_norm = []
    for s in services:
        if s is None:
            continue
        sn = s.strip().upper()
        # 兼容前端可能一次提交逗号分隔字符串的情况
        if "," in sn:
            services_norm.extend([x.strip() for x in sn.split(",") if x.strip()])
        else:
            services_norm.append(sn)
    allowed = {"S1", "S2", "S3", "S4"}
    invalid = [s for s in services_norm if s not in allowed]
    if invalid:
        raise HTTPException(status_code=400, detail=f"invalid services: {invalid}, allowed: {sorted(allowed)}")
    if not services_norm:
        raise HTTPException(status_code=400, detail="services must not be empty")

    services_set = set(services_norm)
    want_s1 = "S1" in services_set
    want_s3 = "S3" in services_set

    logger.info(
        "platform_tasks start — task_name=%s services=%s want_s1=%s want_s3=%s async_scan=%s source_url=%s file=%s",
        task_name,
        services_norm,
        want_s1,
        want_s3,
        async_scan,
        source_url,
        file.filename if file else None,
    )
    
    if (not source_url or not source_url.strip()) and file is None:
        raise HTTPException(status_code=400, detail="provide source_url (git) or file")
    if source_url and source_url.strip() and file is not None:
        raise HTTPException(status_code=400, detail="provide only one of source_url or file")

    file_bytes: Optional[bytes] = None
    upload_filename = ""

    # --- ingest ---
    if source_url and source_url.strip():
        try:
            tree, meta = await ingest_from_url(source_url.strip(), timeout_seconds=300)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        assert file is not None
        file_bytes = await file.read()
        upload_filename = file.filename or "upload.bin"
        tree, meta = await ingest_from_upload(upload_filename, file_bytes)

    source_type = meta.get("source", "unknown")
    source_label = meta.get("url") or meta.get("filename") or ""
    s3_status = meta.get("s3_upload", "Unknown")
    row = FileIngestResult(
        source_type=source_type,
        source_label=(source_label[:512] if source_label else None),
        meta=meta,
        tree=tree,
        s3_upload_status=s3_status,
        status=1,
    )
    db.add(row)
    await db.flush()
    ingest_id = str(row.id)

    logger.info(
        "platform_tasks — ingest complete — ingest_id=%s task_name=%s source_type=%s source_label=%s s3_status=%s",
        ingest_id, task_name, source_type, source_label[:50] if source_label else "N/A", s3_status
    )

    # --- 创建平台任务记录 ---
    pt = PlatformTask(
        task_name=task_name,
        ingest_id=ingest_id,
        s1_status="pending" if "S1" in services_set else "skipped",
        s2_status="pending" if "S2" in services_set else "skipped",
        s3_status="pending" if "S3" in services_set else "skipped",
        s4_status="pending" if "S4" in services_set else "skipped",
        s5_status="pending" if "S5" in services_set else "skipped",
    )
    db.add(pt)
    await db.flush()
    platform_task_id = pt.task_id
    logger.info(
        "platform_tasks — task record created — platform_task_id=%s ingest_id=%s task_name=%s services=%s",
        platform_task_id, ingest_id, task_name, services_norm,
    )

    out: Dict[str, Any] = {
        "status": "success",
        "platform_task_id": platform_task_id,
        "ingest_id": ingest_id,
        "meta": meta,
        "tree": tree,
        "services": services_norm,
        # 兼容旧字段：单服务情况下仍可用于前端展示
        "service": "compliance-sentry" if want_s3 else "none",
    }

    # ===========================================================================
    # S1：OAT 开源合规扫描
    # ===========================================================================
    if want_s1:
        await _handle_s1_scan(
            out=out,
            platform_task_id=platform_task_id,
            task_name=task_name,
            source_url=source_url,
            file_bytes=file_bytes,
            upload_filename=upload_filename,
            branch_tag=branch_tag,
            s1_rule_config_id=s1_rule_config_id,
            async_scan=async_scan,
            db=db,
        )

    if not want_s3:
        logger.info(
        "platform_tasks complete (no compliance-sentry) — ingest_id=%s task_name=%s services=%s",
        ingest_id, task_name, services_norm,
        )
        return out

    if not settings.compliance_sentry_base_url:
        raise HTTPException(status_code=503, detail="COMPLIANCE_SENTRY_BASE_URL not configured")
    
    logger.info("platform_tasks — submitting to sentry — ingest_id=%s task_name=%s async=%s", ingest_id, task_name, async_scan)

    # 自动获取 sentry token，前端无需传 Authorization
    try:
        token = await get_token()
    except RuntimeError as e:
        logger.error("platform_tasks — sentry auth failed — ingest_id=%s error=%s", ingest_id, e)
        raise HTTPException(status_code=503, detail=f"sentry auth failed: {e}")
    sentry_headers = {"Authorization": f"Bearer {token}"}

    if source_url and source_url.strip():
        if async_scan:
            temp_shadow_path = None
            temp_license_shadow_path = None
            if shadow_file is not None:
                shadow_bytes = await shadow_file.read()
                shadow_suffix = Path(shadow_file.filename or "").suffix or ".shadow"
                fd, tmp = tempfile.mkstemp(suffix=shadow_suffix)
                os.close(fd)
                Path(tmp).write_bytes(shadow_bytes)
                temp_shadow_path = tmp
            if license_shadow is not None:
                license_bytes = await license_shadow.read()
                license_suffix = Path(license_shadow.filename or "").suffix or ".license"
                fd, tmp = tempfile.mkstemp(suffix=license_suffix)
                os.close(fd)
                Path(tmp).write_bytes(license_bytes)
                temp_license_shadow_path = tmp
            ar = sentry_mission_task.apply_async(
                kwargs={
                    "mode": "git",
                    "task_name": task_name,
                    "temp_path": None,
                    "git_url": source_url.strip(),
                    "third_party": third_party,
                    "fallback_tree": fallback_tree,
                    "branch_tag": branch_tag,
                    "temp_shadow_path": temp_shadow_path,
                    "temp_license_shadow_path": temp_license_shadow_path,
                    "platform_task_id": platform_task_id,
                },
            )
            out["sentry_async"] = True
            out["platform_task_id"] = ar.id
            logger.info(
                "platform_tasks — submitted to sentry async (git) — ingest_id=%s task_name=%s celery_task_id=%s git_url=%s",
                ingest_id, task_name, ar.id, source_url.strip()
            )
            # running 状态和轮询由 sentry_mission_task 内部在获得 analysis_id 后写库
            return out
        base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
        form_git = {
            "project_name": task_name,
            "git_url": source_url.strip(),
            "third_party": str(third_party).lower(),
            "fallback_tree": str(fallback_tree).lower(),
        }
        if branch_tag:
            form_git["branch_tag"] = branch_tag
        _proxy = settings.compliance_sentry_proxy or None
        files: Dict[str, Any] = {}
        if shadow_file is not None:
            shadow_bytes = await shadow_file.read()
            files["shadow_file"] = (shadow_file.filename or "shadow_file", shadow_bytes, "application/octet-stream")
        if license_shadow is not None:
            license_bytes = await license_shadow.read()
            files["license_shadow"] = (license_shadow.filename or "license_shadow", license_bytes, "application/octet-stream")
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), proxy=_proxy) as client:
            r = await client.post(
                f"{base}/mission/git",
                data=form_git,
                files=files or None,
                headers=sentry_headers,
            )
        try:
            sentry_body = r.json()
        except Exception:
            sentry_body = {"raw": r.text[:2000]}
        out["sentry"] = {"status_code": r.status_code, "body": sentry_body}
        if not r.is_success:
            out["status"] = "error"
            out["error"] = sentry_body
            logger.error(
                "platform_tasks — sentry sync failed (git) — ingest_id=%s task_name=%s status=%d body=%s",
                ingest_id, task_name, r.status_code, str(sentry_body)[:300]
            )
            await _update_platform_task_s3(db, platform_task_id, "failed")
        else:
            analysis_id = sentry_body.get("analysis_id")
            logger.info(
                "platform_tasks — sentry sync accepted (git) — ingest_id=%s task_name=%s analysis_id=%s",
                ingest_id, task_name, analysis_id or "N/A"
            )
            # sentry 返回 202 表示已接受，扫描在 sentry 后台异步进行 → running
            # 同时将 analysis_id 写入 platform_task.s3_analysis_id
            await _update_platform_task_s3(db, platform_task_id, "running", analysis_id)
            # 启动轮询 task，等 sentry 扫描完成后写库
            if analysis_id:
                from app.services.platform_tasks import sentry_poll_task, _POLL_INTERVAL
                sentry_poll_task.apply_async(
                    kwargs={"analysis_id": analysis_id, "platform_task_id": platform_task_id},
                    countdown=_POLL_INTERVAL,
                )
        return out

    # file path — need zip for mission/upload
    assert file_bytes is not None
    fname = upload_filename.lower()
    if not (fname.endswith(".zip") or fname.endswith(".tar.gz") or fname.endswith(".tgz")):
        raise HTTPException(
            status_code=400,
            detail="compliance-sentry mission/upload requires zip/tar.gz; re-upload or use source_url (git)",
        )
    if async_scan:
        fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(fname)[1] or ".zip")
        os.close(fd)
        Path(tmp).write_bytes(file_bytes)
        temp_shadow_path = None
        temp_license_shadow_path = None
        if shadow_file is not None:
            shadow_bytes = await shadow_file.read()
            shadow_suffix = Path(shadow_file.filename or "").suffix or ".shadow"
            fd, tmp_shadow = tempfile.mkstemp(suffix=shadow_suffix)
            os.close(fd)
            Path(tmp_shadow).write_bytes(shadow_bytes)
            temp_shadow_path = tmp_shadow
        if license_shadow is not None:
            license_bytes = await license_shadow.read()
            license_suffix = Path(license_shadow.filename or "").suffix or ".license"
            fd, tmp_license = tempfile.mkstemp(suffix=license_suffix)
            os.close(fd)
            Path(tmp_license).write_bytes(license_bytes)
            temp_license_shadow_path = tmp_license
        ar = sentry_mission_task.apply_async(
            kwargs={
                "mode": "upload",
                "task_name": task_name,
                "temp_path": tmp,
                "git_url": None,
                "third_party": third_party,
                "fallback_tree": fallback_tree,
                "branch_tag": None,
                "temp_shadow_path": temp_shadow_path,
                "temp_license_shadow_path": temp_license_shadow_path,
                "platform_task_id": platform_task_id,
            },
        )
        out["sentry_async"] = True
        out["platform_task_id"] = ar.id
        logger.info(
            "platform_tasks — submitted to sentry async (upload) — ingest_id=%s task_name=%s celery_task_id=%s file=%s",
            ingest_id, task_name, ar.id, upload_filename
        )
        # running 状态和轮询由 sentry_mission_task 内部在获得 analysis_id 后写库
        return out

    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
    _proxy = settings.compliance_sentry_proxy or None
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), proxy=_proxy) as client:
        files: Dict[str, Any] = {"file": (upload_filename or "upload.zip", file_bytes, "application/zip")}
        if shadow_file is not None:
            shadow_bytes = await shadow_file.read()
            files["shadow_file"] = (shadow_file.filename or "shadow_file", shadow_bytes, "application/octet-stream")
        if license_shadow is not None:
            license_bytes = await license_shadow.read()
            files["license_shadow"] = (license_shadow.filename or "license_shadow", license_bytes, "application/octet-stream")
        form = {
            "project_name": task_name,
            "third_party": str(third_party).lower(),
            "fallback_tree": str(fallback_tree).lower(),
        }
        r = await client.post(f"{base}/mission/upload", files=files, data=form, headers=sentry_headers)
    try:
        sentry_body = r.json()
    except Exception:
        sentry_body = {"raw": r.text[:2000]}
    out["sentry"] = {"status_code": r.status_code, "body": sentry_body}
    if not r.is_success:
        out["status"] = "error"
        out["error"] = sentry_body
        logger.error(
            "platform_tasks — sentry sync failed (upload) — ingest_id=%s task_name=%s status=%d body=%s",
            ingest_id, task_name, r.status_code, str(sentry_body)[:300]
        )
        await _update_platform_task_s3(db, platform_task_id, "failed")
    else:
        analysis_id = sentry_body.get("analysis_id")
        logger.info(
            "platform_tasks — sentry sync accepted (upload) — ingest_id=%s task_name=%s analysis_id=%s",
            ingest_id, task_name, analysis_id or "N/A"
        )
        # sentry 返回 202 表示已接受，扫描在 sentry 后台异步进行 → running
        # 同时将 analysis_id 写入 platform_task.s3_analysis_id
        await _update_platform_task_s3(db, platform_task_id, "running", analysis_id)
        if analysis_id:
            from app.services.platform_tasks import sentry_poll_task, _POLL_INTERVAL
            sentry_poll_task.apply_async(
                kwargs={"analysis_id": analysis_id, "platform_task_id": platform_task_id},
                countdown=_POLL_INTERVAL,
            )
    return out


@router.get(
    "/platform/tasks/result/{task_id}",
    summary="查询平台异步任务（Celery）结果",
    tags=["platform"],
)
def platform_task_result(task_id: str) -> Dict[str, Any]:
    """查询平台异步任务（如 sentry 提交）结果，与 Celery backend 一致。"""
    r = AsyncResult(task_id, app=celery_app)
    if r.state in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
        return {"task_id": task_id, "state": r.state}
    if r.state == "FAILURE":
        return {"task_id": task_id, "state": r.state, "error": str(r.result)}
    return {"task_id": task_id, "state": r.state, "result": r.result}


# ===========================================================================
# 网关内部管理  /platform/admin/...
# ===========================================================================

import app.services.sentry_auth as _sentry_auth_module


@router.post(
    "/platform/admin/sentry-token/clear",
    summary="清除网关缓存的 sentry token（下次请求时自动重新登录）",
    tags=["platform-admin"],
)
async def admin_clear_sentry_token() -> Dict[str, Any]:
    """将内存中缓存的 sentry token 清空，使网关在下一次代理请求时强制重新登录获取新 token。"""
    _sentry_auth_module._token = None
    _sentry_auth_module._token_expires_at = 0.0
    logger.info("admin: sentry token cache cleared manually")
    return {"status": "success", "message": "sentry token cache cleared, will re-login on next request"}


@router.post(
    "/platform/admin/sentry-token/refresh",
    summary="强制立即刷新网关缓存的 sentry token",
    tags=["platform-admin"],
)
async def admin_refresh_sentry_token() -> Dict[str, Any]:
    """立即向 sentry 重新登录并更新内存缓存，返回新 token 的过期时间戳。"""
    try:
        await get_token(force_refresh=True)
        import time as _time
        expires_at = _sentry_auth_module._token_expires_at
        remaining = max(0.0, expires_at - _time.monotonic())
        logger.info("admin: sentry token refreshed manually, remaining=%.0fs", remaining)
        return {
            "status": "success",
            "message": "sentry token refreshed successfully",
            "expires_in_seconds": int(remaining),
        }
    except Exception as e:
        logger.error("admin: sentry token refresh failed — %s", e)
        raise HTTPException(status_code=502, detail=f"sentry re-login failed: {e}")


@router.get(
    "/platform/admin/sentry-token/status",
    summary="查看当前网关缓存的 sentry token 状态",
    tags=["platform-admin"],
)
async def admin_sentry_token_status() -> Dict[str, Any]:
    """返回当前缓存 token 是否有效、剩余有效秒数。"""
    import time as _time
    has_token = bool(_sentry_auth_module._token)
    expires_at = _sentry_auth_module._token_expires_at
    remaining = max(0.0, expires_at - _time.monotonic()) if has_token else 0.0
    margin = _sentry_auth_module._TOKEN_MARGIN_SECONDS
    is_valid = has_token and remaining > margin
    return {
        "has_token": has_token,
        "is_valid": is_valid,
        "expires_in_seconds": int(remaining),
        "margin_seconds": margin,
    }


# ===========================================================================
# S1 OAT 扫描辅助函数
# ===========================================================================

async def _handle_s1_scan(
    *,
    out: Dict[str, Any],
    platform_task_id: str,
    task_name: str,
    source_url: Optional[str],
    file_bytes: Optional[bytes],
    upload_filename: str,
    branch_tag: Optional[str],
    s1_rule_config_id: Optional[int],
    async_scan: bool,
    db,
) -> None:
    """
    处理 S1（OAT）扫描逻辑，结果写入 out 字典。

    状态流转（platform_task.s1_status）：
      pending → running（开始扫描前立即写库，让前端可轮询看到进行中状态）
             → success（扫描完成且 oat 无异常）
             → failed（扫描异常/超时/oat 返回错误）

    异步模式（async_scan=True）：投递 Celery 任务后立即返回，Celery 负责所有状态更新。
    同步模式（async_scan=False）：
      - 文件上传：从 file_bytes 解压到临时目录后扫描；
      - Git 地址：重新浅克隆到临时目录后扫描。
    """
    import shutil
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path
    from sqlalchemy import select as _select

    from app.models.oat_rule_config import OatRuleConfig
    from app.models.oat_scan_result import OatScanResult
    from app.models.platform_task import PlatformTask, derive_task_status

    async def _set_s1_status(status: str) -> None:
        """在当前 DB session 中更新 platform_task.s1_status。"""
        res = await db.execute(
            _select(PlatformTask).where(PlatformTask.task_id == platform_task_id)
        )
        pt = res.scalar_one_or_none()
        if pt is not None:
            pt.s1_status = status
            pt.task_status = derive_task_status(pt)
            pt.updated_at = datetime.now(timezone.utc)
        await db.flush()

    # ------------------------------------------------------------------
    # 异步模式：投递 Celery 任务（Celery task 内部负责 running→终态）
    # ------------------------------------------------------------------
    if async_scan:
        if source_url and source_url.strip():
            ar = oat_scan_task.apply_async(
                kwargs={
                    "mode": "git",
                    "project_name": task_name,
                    "platform_task_id": platform_task_id,
                    "rule_config_id": s1_rule_config_id,
                    "git_url": source_url.strip(),
                    "branch_tag": branch_tag,
                },
            )
        else:
            fname_lower = upload_filename.lower()
            suffix = ".tar.gz" if (fname_lower.endswith(".tar.gz") or fname_lower.endswith(".tgz")) else ".zip"
            import os as _os
            fd, tmp_zip = tempfile.mkstemp(suffix=suffix)
            _os.close(fd)
            Path(tmp_zip).write_bytes(file_bytes or b"")
            ar = oat_scan_task.apply_async(
                kwargs={
                    "mode": "upload",
                    "project_name": task_name,
                    "platform_task_id": platform_task_id,
                    "rule_config_id": s1_rule_config_id,
                    "temp_zip_path": tmp_zip,
                },
            )
        out["s1_async"] = True
        out["s1_celery_task_id"] = ar.id
        logger.info(
            "_handle_s1_scan — async submitted — platform_task_id=%s celery_task_id=%s",
            platform_task_id, ar.id,
        )
        return

    # ------------------------------------------------------------------
    # 同步模式
    # 步骤 1：立即标记 running，让前端查询到进行中状态
    # ------------------------------------------------------------------
    await _set_s1_status("running")

    # 步骤 2：预插入 running 状态的 oat_scan_result 记录
    result_row = OatScanResult(
        platform_task_id=platform_task_id,
        rule_config_id=s1_rule_config_id,
        status="running",
    )
    db.add(result_row)
    await db.flush()

    # 步骤 3：读取规则 XML
    rule_xml_content: Optional[str] = None
    if s1_rule_config_id is not None:
        cfg_res = await db.execute(
            _select(OatRuleConfig).where(OatRuleConfig.id == s1_rule_config_id)
        )
        cfg_row = cfg_res.scalar_one_or_none()
        if cfg_row is not None:
            rule_xml_content = cfg_row.xml_content
        else:
            logger.warning(
                "_handle_s1_scan — rule_config_id=%d not found, using builtin defaults",
                s1_rule_config_id,
            )

    src_tmp: Optional[str] = None
    try:
        src_tmp = tempfile.mkdtemp(prefix="oat_src_")
        src_tmp_path = Path(src_tmp)

        if source_url and source_url.strip():
            from app.services.oat_scanner import _clone_repo_sync
            source_dir = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _clone_repo_sync(source_url.strip(), src_tmp_path, branch_tag=branch_tag),
            )
        else:
            from app.services.oat_scanner import _extract_archive_to, _pick_single_root
            extract_dir = src_tmp_path / "extract"
            extract_dir.mkdir()
            archive_path = src_tmp_path / (upload_filename or "upload.zip")
            archive_path.write_bytes(file_bytes or b"")
            _extract_archive_to(archive_path, extract_dir)
            source_dir = _pick_single_root(extract_dir)

        # 步骤 4：执行扫描
        scan_result = await run_oat_scan(
            source_dir,
            task_name,
            rule_xml_content=rule_xml_content,
        )

        scan_error = scan_result.get("error")
        s1_new_status = "failed" if scan_error else "success"

        # 步骤 5：更新 oat_scan_result 为终态
        result_row.status = s1_new_status
        result_row.exit_code = scan_result.get("exit_code")
        result_row.total_issues = scan_result.get("total_issues", 0)
        result_row.invalid_file_type_count = scan_result.get("invalid_file_type_count", 0)
        result_row.license_header_invalid_count = scan_result.get("license_header_invalid_count", 0)
        result_row.copyright_header_invalid_count = scan_result.get("copyright_header_invalid_count", 0)
        result_row.report_text = (scan_result.get("report_text") or "")[:65535]
        result_row.error_message = scan_error
        result_row.updated_at = datetime.now(timezone.utc)

        # 步骤 6：更新 platform_task.s1_status 为终态
        await _set_s1_status(s1_new_status)

        out["s1"] = {
            "status": s1_new_status,
            "total_issues": scan_result.get("total_issues", 0),
            "invalid_file_type_count": scan_result.get("invalid_file_type_count", 0),
            "license_header_invalid_count": scan_result.get("license_header_invalid_count", 0),
            "copyright_header_invalid_count": scan_result.get("copyright_header_invalid_count", 0),
            "rule_config_id": s1_rule_config_id,
        }
        logger.info(
            "_handle_s1_scan sync done — platform_task_id=%s status=%s total_issues=%d",
            platform_task_id, s1_new_status, scan_result.get("total_issues", 0),
        )

    except Exception as exc:
        logger.error(
            "_handle_s1_scan sync error — platform_task_id=%s error=%s",
            platform_task_id, exc,
        )
        out["s1"] = {"status": "failed", "error": str(exc)}
        try:
            result_row.status = "failed"
            result_row.error_message = str(exc)[:2000]
            result_row.updated_at = datetime.now(timezone.utc)
            await _set_s1_status("failed")
        except Exception:
            pass
    finally:
        if src_tmp:
            shutil.rmtree(src_tmp, ignore_errors=True)
