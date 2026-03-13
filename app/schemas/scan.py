from __future__ import annotations

"""
API 请求/响应模型（Pydantic）。

作用：
- 给前端提供稳定的字段约定；
- 便于后续自动生成 OpenAPI 文档、做参数校验与类型提示。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    """前端提交的扫描请求。"""

    target: str = Field(..., description="扫描目标（例如域名/IP/仓库/项目ID等）")
    options: Dict[str, Any] = Field(default_factory=dict, description="透传给下游模块的可选参数")
    modules: Optional[List[str]] = Field(default=None, description="指定扫描模块：a/b/c/d；为空表示全扫")


class ModuleResult(BaseModel):
    """单个模块的执行结果（网关聚合用）。"""

    module: str
    ok: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    elapsed_ms: int


class ScanResult(BaseModel):
    """一次扫描的聚合结果（可用于扩展 /scan/result 的结构化返回）。"""

    request_id: str
    target: str
    results: List[ModuleResult]

