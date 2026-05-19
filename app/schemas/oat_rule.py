"""
OAT 规则配置与扫描结果相关 Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# OAT 规则配置 CRUD
# ---------------------------------------------------------------------------

class OatRuleConfigCreate(BaseModel):
    """创建 OAT 规则配置请求体。"""

    name: str = Field(..., max_length=255, description="规则配置名称（全局唯一）")
    description: Optional[str] = Field(
        None, max_length=500, description="规则配置描述"
    )
    xml_content: Optional[str] = Field(
        None,
        description=(
            "OAT XML 规则内容（完整 XML 字符串）。"
            "该内容将在 S1 扫描时通过 -oatconfig 传入 oat_python，"
            "叠加到其内置 OAT-Default.xml 之上。"
            "留空表示仅使用 oat_python 内置默认规则，不做任何叠加。"
        ),
    )
    is_active: bool = Field(True, description="是否启用（false 时前端不可选用）")


class OatRuleConfigUpdate(BaseModel):
    """更新 OAT 规则配置请求体（所有字段可选）。"""

    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    xml_content: Optional[str] = None
    is_active: Optional[bool] = None


class OatRuleConfigResponse(BaseModel):
    """OAT 规则配置详情响应。"""

    id: int
    name: str
    description: Optional[str]
    xml_content: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OatRuleConfigListResponse(BaseModel):
    """OAT 规则配置列表响应。"""

    total: int
    items: List[OatRuleConfigResponse]


# ---------------------------------------------------------------------------
# 内置默认 XML 查询响应
# ---------------------------------------------------------------------------

class BuiltinXmlResponse(BaseModel):
    """oat_python 内置默认规则 XML 内容（只读参考）。"""

    filename: str = Field(..., description="XML 文件名")
    xml_content: str = Field(..., description="XML 文件内容")


# ---------------------------------------------------------------------------
# OAT 扫描结果
# ---------------------------------------------------------------------------

class OatIssueItem(BaseModel):
    """单条 OAT issue 条目（三类问题通用）。"""

    file: str = Field(..., description="问题文件路径（相对仓库根）")
    content: str = Field(
        ...,
        description=(
            "issue 具体内容：\n"
            "  Invalid File Type       → 文件类型（如 unknown / binary）\n"
            "  License Header Invalid  → 许可证标识（如 NoLicenseHeader / GPL-2.0-only）\n"
            "  Copyright Header Invalid → 版权声明内容（如 NULL / Copyright 2024 Xxx）"
        ),
    )
    project: str = Field(..., description="oat 扫描时使用的项目名称")


class OatScanResultResponse(BaseModel):
    """OAT 扫描结果详情响应（含三类 issue 结构化列表）。"""

    id: int
    platform_task_id: str
    rule_config_id: Optional[int]
    celery_task_id: Optional[str]
    status: str
    exit_code: Optional[int]
    total_issues: int
    invalid_file_type_count: int
    license_header_invalid_count: int
    copyright_header_invalid_count: int
    # 三类问题结构化列表（None 表示旧数据尚未解析）
    invalid_file_type_issues: Optional[List[OatIssueItem]] = None
    license_header_invalid_issues: Optional[List[OatIssueItem]] = None
    copyright_header_invalid_issues: Optional[List[OatIssueItem]] = None
    report_text: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OatScanResultListItem(BaseModel):
    """
    OAT 扫描任务列表中的单条摘要。

    不含 report_text（原始报告文本）和三类 issue 详情数组，减少列表传输量。
    需要 issue 详情请通过 GET /platform/oat-scan-results/{task_id} 获取完整响应。
    """

    id: int
    platform_task_id: str
    rule_config_id: Optional[int]
    celery_task_id: Optional[str]
    status: str
    exit_code: Optional[int]
    total_issues: int
    invalid_file_type_count: int
    license_header_invalid_count: int
    copyright_header_invalid_count: int
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OatScanResultListResponse(BaseModel):
    """OAT 扫描任务列表响应。"""

    total: int = Field(..., description="符合筛选条件的总记录数")
    page: int = Field(..., description="当前页码（从 1 起）")
    page_size: int = Field(..., description="每页记录数")
    total_pages: int = Field(..., description="总页数")
    items: List[OatScanResultListItem]
