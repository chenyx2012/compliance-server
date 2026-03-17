from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

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

    # MySQL：文件目录树等持久化
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="root", alias="MYSQL_USER")
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="compliance_gateway", alias="MYSQL_DATABASE")
    mysql_charset: str = Field(default="utf8mb4", alias="MYSQL_CHARSET")

    # S3 上传（解压后调用 s3_uploader.py）；app_token 为空则不执行上传
    s3_app_token: Optional[str] = Field(default=None, alias="S3_APP_TOKEN")
    s3_region: str = Field(default="cn-east-3", alias="S3_REGION")
    s3_bucket_name: str = Field(default="", alias="S3_BUCKET_NAME")
    s3_bucket_path: str = Field(default="csv/", alias="S3_BUCKET_PATH")
    s3_uploader_script: str = Field(default="s3_uploader.py", alias="S3_UPLOADER_SCRIPT")

    @property
    def database_url(self) -> str:
        """异步驱动 aiomysql，用于 SQLAlchemy create_async_engine。"""
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+aiomysql://{user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset={self.mysql_charset}"
        )


settings = Settings()

