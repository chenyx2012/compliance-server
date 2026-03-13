from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore"：允许 .env 中存在未声明字段，避免扩展时影响启动
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="compliance-gateway", alias="APP_NAME")
    env: str = Field(default="dev", alias="ENV")

    # 四类下游扫描服务（A/B/C/D）通用配置，每类可独立部署、互不依赖
    # BaseURL：服务根地址，生产通过环境变量覆盖
    service_a_base_url: str = Field(default="http://127.0.0.1:9001", alias="SERVICE_A_BASE_URL")
    service_b_base_url: str = Field(default="http://127.0.0.1:9002", alias="SERVICE_B_BASE_URL")
    service_c_base_url: str = Field(default="http://127.0.0.1:9003", alias="SERVICE_C_BASE_URL")
    service_d_base_url: str = Field(default="http://127.0.0.1:9004", alias="SERVICE_D_BASE_URL")
    # 每类服务的扫描入口 path（可与其它类不同，按各自服务约定配置）
    service_a_scan_path: str = Field(default="/scan", alias="SERVICE_A_SCAN_PATH")
    service_b_scan_path: str = Field(default="/scan", alias="SERVICE_B_SCAN_PATH")
    service_c_scan_path: str = Field(default="/scan", alias="SERVICE_C_SCAN_PATH")
    service_d_scan_path: str = Field(default="/scan", alias="SERVICE_D_SCAN_PATH")
    # 全局下游超时（秒）；若某类服务需单独超时，可设 SERVICE_X_TIMEOUT_SECONDS
    upstream_timeout_seconds: float = Field(default=30, alias="UPSTREAM_TIMEOUT_SECONDS")
    service_a_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_A_TIMEOUT_SECONDS")
    service_b_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_B_TIMEOUT_SECONDS")
    service_c_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_C_TIMEOUT_SECONDS")
    service_d_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_D_TIMEOUT_SECONDS")

    # Redis：同时作为 Celery broker（队列）与 backend（结果存储）
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    celery_task_queue: str = Field(default="compliance_scan", alias="CELERY_TASK_QUEUE")
    celery_result_expires_seconds: int = Field(default=86400, alias="CELERY_RESULT_EXPIRES_SECONDS")


settings = Settings()

