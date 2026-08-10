"""EatWhat 后端配置分层。

基于 pydantic-settings：字段名与根目录 `.env.example` 对齐，
环境变量名自动映射（如 `DATABASE_URL` -> `database_url`）。
非法环境值在启动时直接抛校验错误，即"配置分层校验"。
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
AppMode = Literal["mock", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = "development"
    app_mode: AppMode = "mock"

    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_issuer: str = ""
    supabase_jwt_audience: str = ""

    ai_provider: str = "mock"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_daily_user_limit: int = 3
    ai_global_daily_limit: int = 100
    ai_max_retries: int = 1

    poi_provider: Literal["mock", "live", "auto"] = "mock"
    amap_api_key: str = ""
    poi_cache_ttl_seconds: int = 1200

    frontend_origins: str = "http://localhost:5173"
    public_min_sample_size: int = 3

    @property
    def frontend_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def secret_values(self) -> dict[str, str]:
        """脱敏过滤器使用的敏感值清单（只含非空值）。
        所有非空密钥会被日志 RedactFilter 统一替换为 "***REDACTED***"，
        禁止任何生产 / 测试日志出现明文。"""
        candidates = {
            "database_url": self.database_url,
            "supabase_url": self.supabase_url,
            "supabase_jwks_url": self.supabase_jwks_url,
            "supabase_anon_key": self.supabase_anon_key,
            "supabase_service_role_key": self.supabase_service_role_key,
            "ai_api_key": self.ai_api_key,
            "amap_api_key": self.amap_api_key,
        }
        return {key: value for key, value in candidates.items() if value}

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
