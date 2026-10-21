from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="compliance-gateway", alias="APP_NAME")
    env: str = Field(default="dev", alias="ENV")

    # 四类下游扫描服务（A/B/C/D）
    service_a_base_url: str = Field(default="http://127.0.0.1:9001", alias="SERVICE_A_BASE_URL")
    service_b_base_url: str = Field(default="http://127.0.0.1:9002", alias="SERVICE_B_BASE_URL")
    service_c_base_url: str = Field(default="http://127.0.0.1:9003", alias="SERVICE_C_BASE_URL")
    service_d_base_url: str = Field(default="http://127.0.0.1:9004", alias="SERVICE_D_BASE_URL")
    service_a_scan_path: str = Field(default="/scan", alias="SERVICE_A_SCAN_PATH")
    service_b_scan_path: str = Field(default="/scan", alias="SERVICE_B_SCAN_PATH")
    service_c_scan_path: str = Field(default="/scan", alias="SERVICE_C_SCAN_PATH")
    service_d_scan_path: str = Field(default="/scan", alias="SERVICE_D_SCAN_PATH")
    upstream_timeout_seconds: float = Field(default=30, alias="UPSTREAM_TIMEOUT_SECONDS")
    service_a_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_A_TIMEOUT_SECONDS")
    service_b_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_B_TIMEOUT_SECONDS")
    service_c_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_C_TIMEOUT_SECONDS")
    service_d_timeout_seconds: Optional[float] = Field(default=None, alias="SERVICE_D_TIMEOUT_SECONDS")

    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    celery_task_queue: str = Field(default="compliance_scan", alias="CELERY_TASK_QUEUE")
    celery_queue_scan_pr: str = Field(default="scan_pr", alias="CELERY_QUEUE_SCAN_PR")
    celery_queue_scan_version: str = Field(default="scan_version", alias="CELERY_QUEUE_SCAN_VERSION")
    celery_queue_parse: str = Field(default="parse", alias="CELERY_QUEUE_PARSE")
    celery_queue_import: str = Field(default="import", alias="CELERY_QUEUE_IMPORT")
    celery_result_expires_seconds: int = Field(default=86400, alias="CELERY_RESULT_EXPIRES_SECONDS")
    # S2 异步受理超时（秒）；真 SSCD 同步模式仍可用长超时
    s2_accept_timeout_seconds: float = Field(default=120.0, alias="S2_ACCEPT_TIMEOUT_SECONDS")
    # notify 入队失败是否返回 503 让扫描侧重试
    notify_require_enqueue: bool = Field(default=True, alias="NOTIFY_REQUIRE_ENQUEUE")
    # PR 后处理：是否跑开源/自研导入（默认否）
    pr_postprocess_imports: bool = Field(default=False, alias="PR_POSTPROCESS_IMPORTS")
    # PR 是否跑 AI 复核（默认是）
    pr_postprocess_ai_verify: bool = Field(default=True, alias="PR_POSTPROCESS_AI_VERIFY")

    temp_storage_root: str = Field(default="/tmp", alias="TEMP_STORAGE_ROOT")

    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="root", alias="MYSQL_USER")
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="compliance_gateway", alias="MYSQL_DATABASE")
    mysql_charset: str = Field(default="utf8mb4", alias="MYSQL_CHARSET")

    # S3 上传（解压后调 s3_uploader.py）；token 为空则不上传
    s3_uploader_token: Optional[str] = Field(default=None, alias="S3_UPLOADER_TOKEN")
    s3_uploader_region: str = Field(default="cn-east-3", alias="S3_UPLOADER_REGION")
    s3_uploader_bucket_name: str = Field(default="", alias="S3_UPLOADER_BUCKET_NAME")
    s3_uploader_bucket_path: str = Field(default="csv/", alias="S3_UPLOADER_BUCKET_PATH")
    s3_uploader_script: str = Field(default="s3_uploader.py", alias="S3_UPLOADER_SCRIPT")

    compliance_sentry_base_url: str = Field(
        default="http://127.0.0.1:8010",
        alias="COMPLIANCE_SENTRY_BASE_URL",
    )
    compliance_sentry_username: str = Field(default="admin", alias="COMPLIANCE_SENTRY_USERNAME")
    compliance_sentry_password: str = Field(default="", alias="COMPLIANCE_SENTRY_PASSWORD")
    compliance_sentry_proxy: str = Field(default="", alias="COMPLIANCE_SENTRY_PROXY")

    # S3 配置（华为云 CSB）——下载扫描结果文件
    s3_app_token: str = Field(default="", alias="S3_APP_TOKEN")
    s3_region: str = Field(default="cn-east-3", alias="S3_REGION")
    s3_bucket_name: str = Field(default="compliance", alias="S3_BUCKET_NAME")
    s3_bucket_id: Optional[str] = Field(default=None, alias="S3_BUCKET_ID")
    s3_bucket_path: str = Field(default="csv/", alias="S3_BUCKET_PATH")
    s3_vendor: str = Field(default="HEC", alias="S3_VENDOR")
    s3_shared: bool = Field(default=False, alias="S3_SHARED")
    s3_download_path: str = Field(default="temp/scan_results", alias="S3_DOWNLOAD_PATH")
    # S3 兼容 endpoint（MinIO / 华为 OBS / 标准 S3）；空则用公有云默认
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    # MinIO 建议 true（path-style）
    s3_path_style: bool = Field(default=True, alias="S3_PATH_STYLE")
    s3_auto_create_bucket: bool = Field(default=True, alias="S3_AUTO_CREATE_BUCKET")

    ai_code_scan_service_url: str = Field(
        default="http://127.0.0.1:8002",
        alias="AI_CODE_SCAN_SERVICE_URL",
    )
    osc_notify_url: str = Field(
        default="http://127.0.0.1:8000/api/scan/notify",
        alias="OSC_NOTIFY_URL",
    )
    cors_origins: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    open_source_cpp_path: str = Field(default="D:\\OpenSourceCodeNvidia\\cpp", alias="OPEN_SOURCE_CPP_PATH")
    open_source_java_path: str = Field(default="D:\\OpenSourceCodeNvidia\\java", alias="OPEN_SOURCE_JAVA_PATH")
    open_source_python_path: str = Field(
        default="/data/OpenSource/opensource/python",
        alias="OPEN_SOURCE_PYTHON_PATH",
    )
    open_source_nvidia_base_path: str = Field(
        default="D:\\OpenSourceCodeNvidia\\nvidia\\nvidia",
        alias="OPEN_SOURCE_NVIDIA_BASE_PATH",
    )

    path1_prefix: str = Field(default="work/", alias="PATH1_PREFIX")
    path2_prefix: str = Field(default="ccfinderX/", alias="PATH2_PREFIX")
    path2_prefix2: str = Field(default="opensource_code/", alias="PATH2_PREFIX2")

    self_developed_base_path: str = Field(default="/data/tmp", alias="SELF_DEVELOPED_BASE_PATH")
    self_developed_repo_name: str = Field(default="cann", alias="SELF_DEVELOPED_REPO_NAME")
    self_developed_file_types: str = Field(
        default=".cpp,.h,.hpp,.c,.cc,.java,.py",
        alias="SELF_DEVELOPED_FILE_TYPES",
    )

    ai_model_default: str = Field(default="Qwen-V3.6-35B-A3B", alias="AI_MODEL_DEFAULT")

    # 上传凭据：uploader_token → app_token → access_key（MinIO）
    @property
    def effective_s3_uploader_token(self) -> Optional[str]:
        return (
            self.s3_uploader_token
            or (self.s3_app_token or None)
            or (self.s3_access_key or None)
        )

    @property
    def effective_s3_uploader_bucket_name(self) -> str:
        return self.s3_uploader_bucket_name or self.s3_bucket_name

    @property
    def effective_s3_uploader_region(self) -> str:
        return self.s3_uploader_region or self.s3_region

    @property
    def effective_s3_uploader_bucket_path(self) -> str:
        return self.s3_uploader_bucket_path or self.s3_bucket_path

    @property
    def database_url(self) -> str:
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+aiomysql://{user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset={self.mysql_charset}"
        )


settings = Settings()
