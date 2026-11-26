"""全局配置：唯一入口 get_settings()，来源优先级 环境变量 > backend/.env > 默认值。

降级预案内建（CODING_PLAN §0.7）：重型机制一律做成配置项，不写死。
测试环境通过 ENV_FILE="" 关闭 .env 加载，确保不触碰真实外部服务。
"""
import os
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# 环境变量 ENV_FILE 可覆盖 .env 路径；设为空串则完全不加载文件
_ENV_FILE = os.environ.get("ENV_FILE", ".env")
if _ENV_FILE in ("", '""', "''"):
    _ENV_FILE = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "netdisk"
    app_version: str = "0.1.0-m0"
    env: str = "dev"
    debug: bool = True
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- MySQL ----------
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "netdisk"
    mysql_password: str = "netdisk"
    mysql_database: str = "netdisk"

    # ---------- Redis ----------
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # ---------- MinIO ----------
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "netdisk"
    minio_secret_key: str = "netdisk123456"
    minio_secure: bool = False
    minio_bucket_formal: str = "netdisk-formal"
    minio_bucket_candidate: str = "netdisk-candidate"
    minio_pool_workers: int = 16
    minio_call_timeout: float = 10.0
    bucket_init_retries: int = 3
    bucket_init_delay_seconds: float = 2.0

    # ---------- 安全（M1 生效）----------
    jwt_secret_key: str = "change_me_random_32_bytes"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 14
    login_fail_threshold: int = 5
    login_fail_window_seconds: int = 900
    login_lock_max_seconds: int = 1800
    password_hash_rounds: int = 12

    # ---------- 上传限制（M3 生效）----------
    upload_max_file_size: int = 5 * 1024**3  # 5GiB
    upload_chunk_size: int = 8 * 1024**2  # 8MiB
    upload_chunk_concurrency: int = 4
    upload_type_whitelist: str = (
        "jpg,jpeg,png,gif,webp,bmp,ico,pdf,txt,md,csv,json,log,"
        "doc,docx,xls,xlsx,ppt,pptx,zip,rar,7z,tar,gz,mp4,mp3,wav,mkv"
    )

    # ---------- 治理参数（降级预案配置化）----------
    trash_retention_days: int = 30
    candidate_grace_hours: int = 24
    upload_task_ttl_hours: int = 168
    share_transfer_recheck_seconds: int = 3
    download_rate_limit_bytes: int = 2 * 1024**2  # 2MiB/s
    download_rate_burst_bytes: int = 8 * 1024**2
    zip_max_entries: int = 100
    zip_max_total_bytes: int = 2 * 1024**3  # 2GiB
    share_session_ttl_hours: int = 24
    share_preview_text_max_bytes: int = 2 * 1024**2  # 文本预览上限 2MiB，超出强制下载

    # ---------- 派生 ----------
    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{quote_plus(self.mysql_user)}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{quote_plus(self.redis_password)}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_type_whitelist_set(self) -> set[str]:
        return {e.strip().lower() for e in self.upload_type_whitelist.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
