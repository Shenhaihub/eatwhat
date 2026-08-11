"""EatWhat 后端配置分层。

基于 pydantic-settings：字段名与根目录 `.env.example` 对齐，
环境变量名自动映射（如 `DATABASE_URL` -> `database_url`）。
非法环境值在启动时直接抛校验错误，即"配置分层校验"。

AI API Key 安全原则（P5-03B）：
    - `ai_api_key` 只允许 空字符串 或 `ENC:<fernet-token>` 格式；
      运行期由 `app.core.encryption.resolve_encrypted_api_key()` 按需解密，
      明文 key 只存在内存局部变量，绝不写入 settings 单例或缓存。
    - 口令 `ew_ai_key_passphrase` 与 `ai_api_key` 是两个独立变量，
      必须分别填写；即使 .env 文件被整个窃取，没有口令也无法还原。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
AppMode = Literal["mock", "live"]

_ENC_PREFIX = "ENC:"


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

    # ============== AI Provider（P5 动态 AI，选型：DeepSeek V4 Flash）==============
    # 取值："mock"（默认，纯规则/MockAIProvider 契约） / "deepseek"（启用 Live）
    # / "auto"（优先 deepseek，失败时 request-level 走 Mock；auto 与 mock 对用户无感）
    ai_provider: Literal["mock", "deepseek", "auto"] = "mock"

    # AI API Key：**严禁明文**，必须使用 backend/scripts/encrypt_ai_key.py
    # 加密为 `ENC:<Fernet-Token>` 格式后粘贴；空字符串 = 未配置，强制走 mock。
    ai_api_key: str = ""

    # 解密口令：至少 12 字符；请使用密码管理器生成 32+ 随机字符。
    # ⚠️ 不要与 AI key 本身、任何账号密码复用。
    ew_ai_key_passphrase: str = ""

    # （极少用）PBKDF2 salt 覆盖，默认使用 encryption.DEFAULT_EW_SALT。
    # 多租户部署或合规特殊需求才填写；个人 demo 留空即可。
    ew_ai_salt: str = ""

    # 模型名（OpenAI 兼容格式）：deepseek 官方最便宜 V4 Flash
    ai_model: str = "deepseek-v4-flash"

    # MockAIProvider 调试参数（只在 ai_provider=mock/auto 无密文时生效）
    # 模式：normal（默认正常 JSON）/ slow（9s 延迟，模拟超时）
    #       / invalid_json（损坏 JSON）/ out_of_bounds_food_code（越界 food_code）
    mock_ai_mode: Literal[
        "normal", "slow", "invalid_json", "out_of_bounds_food_code"
    ] = "normal"

    # seed：用于 normal 模式下扰动 5 候选排序（MEM-024：不同 seed → 不同首候选）
    mock_ai_seed: int = Field(default=0, ge=0, le=10_000)

    # 额度/限流（先记录配置，P5-05 账本实现再实际启用）
    ai_daily_user_limit: int = 3
    ai_global_daily_limit: int = 100
    ai_max_retries: int = 1

    # ============== POI ==============
    poi_provider: Literal["mock", "live", "auto"] = "mock"
    amap_api_key: str = ""
    poi_cache_ttl_seconds: int = 1200

    # ============== Redis（P5-07B 多 worker 限流；不填 = 用进程内 TTLCache）==============
    # 格式：redis://[[username]:[password]]@host:port[/db]
    # 示例 1：本地默认无密码单实例 → redis://localhost:6379/0
    # 示例 2：Sentinel/Cluster 请先改造 RedisRateLimiter，本字段目前只支持单节点
    redis_url: str = ""

    frontend_origins: str = "http://localhost:5173"
    public_min_sample_size: int = 3

    @field_validator("ai_api_key")
    @classmethod
    def _validate_ai_api_key_is_enc_or_empty(cls, v: object) -> object:
        """启动期 fail-fast，防止明文 sk-... 意外入库。"""
        if v is None:
            return ""
        if not isinstance(v, str):
            raise TypeError("AI_API_KEY 必须是字符串（留空 = 未配置，或 ENC: 开头的密文）。")
        s = v.strip()
        if not s:
            return ""
        if s.startswith("sk-"):
            raise ValueError(
                "AI_API_KEY 疑似明文（sk- 前缀），禁止写入 .env！"
                "请运行 backend/scripts/encrypt_ai_key.py 加密后粘贴 ENC:xxx。"
            )
        if not s.startswith(_ENC_PREFIX):
            raise ValueError(
                "AI_API_KEY 配置错误：要么留空（走 Mock），要么必须以 ENC: 开头后跟密文。"
            )
        return s

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
            # 口令本身也是极高敏感值
            "ew_ai_key_passphrase": self.ew_ai_key_passphrase,
            "ew_ai_salt": self.ew_ai_salt,
        }
        return {key: value for key, value in candidates.items() if value}

    @property
    def database_configured(self) -> bool:
        return bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()

