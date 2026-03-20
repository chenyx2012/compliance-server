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

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import get_db
from app.models.file_ingest import FileIngestResult
from app.services.file_ingest import ingest_from_upload, ingest_from_url
from app.services.platform_tasks import sentry_mission_task
from app.services.sentry_auth import get_token
from app.services.sentry_proxy import proxy_to_sentry

router = APIRouter(tags=["compliance-sentry", "platform"])

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _sentry_base() -> str:
    """返回 sentry 基础 URL，未配置时抛 503。"""
    if not settings.compliance_sentry_base_url:
        raise HTTPException(status_code=503, detail="COMPLIANCE_SENTRY_BASE_URL not configured")
    return settings.compliance_sentry_base_url


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
async def sentry_analysis_delete(analysis_id: str, request: Request):
    return await proxy_to_sentry(_sentry_base(), f"analysis/{analysis_id}", request)


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
    summary="平台任务总入口（文件入库 + 可选触发 sentry 扫描）",
    tags=["platform"],
)
async def platform_tasks(
    project_name: str = Form(..., description="任务/项目名称（提交 sentry 必填）"),
    service: str = Form(
        "none",
        description="扫描服务：none 仅入库目录树；compliance-sentry 额外提交 sentry 分析任务",
    ),
    async_scan: bool = Form(False, description="为 true 时 sentry 提交走 Celery，立即返回 task_id"),
    source_url: Optional[str] = Form(None, description="Git 仓库地址（与 file 二选一）"),
    file: Optional[UploadFile] = File(None, description="上传 zip 等（与 source_url 二选一）"),
    third_party: bool = Form(False),
    fallback_tree: bool = Form(False),
    branch_tag: Optional[str] = Form(None, description="git 任务可选分支"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    平台任务总入口（支持异步）：
    1. 根据 file 或 source_url 拉取代码并解析目录树，写入 MySQL（ingest_id）；
    2. 若 service=compliance-sentry：再向 sentry 提交 mission/upload（zip）或 mission/git（仓库地址）。
    """
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
    ingest_id = row.id

    out: Dict[str, Any] = {
        "ok": True,
        "ingest_id": ingest_id,
        "meta": meta,
        "tree": tree,
        "service": service,
    }

    svc = (service or "none").strip().lower()
    if svc != "compliance-sentry":
        return out

    if not settings.compliance_sentry_base_url:
        raise HTTPException(status_code=503, detail="COMPLIANCE_SENTRY_BASE_URL not configured")

    # 自动获取 sentry token，前端无需传 Authorization
    try:
        token = await get_token()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sentry auth failed: {e}")
    sentry_headers = {"Authorization": f"Bearer {token}"}

    if source_url and source_url.strip():
        if async_scan:
            ar = sentry_mission_task.apply_async(
                kwargs={
                    "mode": "git",
                    "project_name": project_name,
                    "temp_path": None,
                    "git_url": source_url.strip(),
                    "third_party": third_party,
                    "fallback_tree": fallback_tree,
                    "branch_tag": branch_tag,
                },
            )
            out["sentry_async"] = True
            out["platform_task_id"] = ar.id
            return out
        base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
        form_git = {
            "project_name": project_name,
            "git_url": source_url.strip(),
            "third_party": str(third_party).lower(),
            "fallback_tree": str(fallback_tree).lower(),
        }
        if branch_tag:
            form_git["branch_tag"] = branch_tag
        _proxy = settings.compliance_sentry_proxy or None
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), proxy=_proxy) as client:
            r = await client.post(f"{base}/mission/git", data=form_git, headers=sentry_headers)
        try:
            sentry_body = r.json()
        except Exception:
            sentry_body = {"raw": r.text[:2000]}
        out["sentry"] = {"status_code": r.status_code, "body": sentry_body}
        if not r.is_success:
            out["ok"] = False
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
        ar = sentry_mission_task.apply_async(
            kwargs={
                "mode": "upload",
                "project_name": project_name,
                "temp_path": tmp,
                "git_url": None,
                "third_party": third_party,
                "fallback_tree": fallback_tree,
                "branch_tag": None,
            },
        )
        out["sentry_async"] = True
        out["platform_task_id"] = ar.id
        return out

    base = settings.compliance_sentry_base_url.rstrip("/") + "/api/v1"
    _proxy = settings.compliance_sentry_proxy or None
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0), proxy=_proxy) as client:
        files = {"file": (upload_filename or "upload.zip", file_bytes, "application/zip")}
        form = {
            "project_name": project_name,
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
        out["ok"] = False
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
