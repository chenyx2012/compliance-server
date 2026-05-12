"""
OAT 规则配置与扫描结果相关 Pydantic 模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

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

class OatScanResultResponse(BaseModel):
    """OAT 扫描结果详情响应。"""

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
    report_text: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
