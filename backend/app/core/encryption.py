"""AI API Key 多层加密与解密工具。

设计原则（安全底线，改动前必须先过安全评审）：
1. 明文 key 永不落盘、永不写入 .env、永不进入日志/错误/异常 traceback。
2. 环境变量中存储的密文格式严格为 ``ENC:<fernet-token-base64>``。
   若缺失 ``ENC:`` 前缀，直接判定为"疑似明文配置"，启动期 fail-fast 抛错。
3. 解密仅在需要创建 AI 客户端的瞬间发生（按需解密），结果只存在内存局部变量；
   禁止把明文 key 存入 settings 单例、全局缓存或任何持久化对象。
4. Fernet 密钥 **不从** 环境变量直接获取，而是使用 PBKDF2HMAC 从
   ``EW_AI_KEY_PASSPHRASE``（口令）+ 固定 salt（``EW_AI_SALT`` 环境变量可
   覆盖，默认使用本模块硬编码的项目级 salt）派生。
   这样即使 .env 文件被整个窃取，没有 passphrase 也无法还原。
5. 错误消息只使用"AI Key 解密失败"等通用文案，**绝不包含** 密文片段、
   明文片段、盐或口令的任何部分。
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Final

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger("app.core.encryption")

ENC_PREFIX: Final[str] = "ENC:"
DEFAULT_EW_SALT: Final[bytes] = b"eatwhat-ai-key-salt-v1\x00\x9f\xe2"
PBKDF2_ITERATIONS: Final[int] = 480_000


class AIKeyEncryptionError(RuntimeError):
    """AI Key 加密/解密通用错误。

    所有 raise 点都必须用该类（或其字面等价文案）包装原始异常，
    禁止外部直接捕获到 cryptography 原生异常并暴露给接口层。
    原始异常只写 app log（且经过 RedactFilter 脱敏）。"""


def _passphrase_to_fernet_key(*, passphrase: str, salt: bytes) -> bytes:
    """使用 PBKDF2HMAC 把用户口令派生成 Fernet 需要的 32 字节 key。

    - 算法：SHA-256
    - 迭代次数：480,000（OWASP 2023+ 推荐值）
    - 输出长度：32 字节 → base64(url-safe) 编码，即 Fernet 构造要求的格式。
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    derived = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def resolve_encrypted_api_key(
    *,
    encrypted_value: str,
    passphrase: str | None,
    salt_override: str | None = None,
) -> str | None:
    """解密 AI API Key。

    参数：
        encrypted_value: 来自 settings.ai_api_key 的原始字符串。
            合法值为 "" / None（表示未配置，返回 None 让调用方走 Mock/规则）
            或 ``ENC:<fernet-token>``。
            若以其他格式出现（疑似明文 sk-...）直接抛错。
        passphrase: 口令。若 encrypted_value 为 ENC: 形态但 passphrase 为空，
            抛 AIKeyEncryptionError。
        salt_override: 可选；覆盖默认项目级 salt。仅允许企业/私有化部署场景，
            默认个人 demo 直接使用 DEFAULT_EW_SALT 即可（无需在 .env 里再填一项）。

    返回：
        明文 key（只在调用栈的局部变量中使用，尽快交给 httpx header，不缓存）。
        当 encrypted_value 为空/未配置时，返回 None。
    """
    if not encrypted_value:
        return None

    # 1) 前缀强校验：禁止明文直接落 .env
    if not encrypted_value.startswith(ENC_PREFIX):
        if encrypted_value.startswith("sk-"):
            raise AIKeyEncryptionError(
                "AI_API_KEY 疑似明文（sk- 前缀），禁止直接写入 .env。"
                "请运行 backend/scripts/encrypt_ai_key.py 加密后，把输出的 ENC:xxx 粘到 .env。"
            )
        raise AIKeyEncryptionError(
            "AI_API_KEY 格式非法：未配置时留空；配置时必须以 ENC: 开头后跟加密密文。"
        )

    if not passphrase:
        raise AIKeyEncryptionError(
            "AI_API_KEY 需要解密，但 EW_AI_KEY_PASSPHRASE 为空。"
            "请在 .env 中配置口令（不要与任何密码/API key 复用）。"
        )

    token_str = encrypted_value[len(ENC_PREFIX):]
    try:
        token_bytes = token_str.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AIKeyEncryptionError("AI API Key 密文包含非 ASCII 字符。") from exc

    salt = DEFAULT_EW_SALT
    if salt_override:
        salt = salt_override.encode("utf-8")

    try:
        fernet_key = _passphrase_to_fernet_key(passphrase=passphrase, salt=salt)
        f = Fernet(fernet_key)
        plaintext_bytes = f.decrypt(token_bytes)
    except InvalidToken as exc:
        # InvalidToken = 口令错误 / 密文被篡改 / 密文过期（Fernet 带 TTL 默认不启用）
        # 统一给出通用文案，避免区分"口令错"vs"密文错"给攻击者提供 Oracle
        raise AIKeyEncryptionError("AI API Key 解密失败（口令不对或密文损坏）。") from exc
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise AIKeyEncryptionError("AI API Key 密文 base64 格式损坏。") from exc

    try:
        plaintext = plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AIKeyEncryptionError("AI API Key 解密结果不是 UTF-8 文本。") from exc

    if not plaintext:
        raise AIKeyEncryptionError("AI API Key 解密结果为空。")

    # 最终安全断言：明文不以 ENC: 开头（防止用户把密文又加密了一遍，嵌套错误）
    if plaintext.startswith(ENC_PREFIX):
        raise AIKeyEncryptionError(
            "AI API Key 解密后仍然以 ENC: 开头，疑似重复加密，请重新只加密一次明文 sk-...。"
        )

    return plaintext


def encrypt_api_key_for_env(
    *,
    plaintext_key: str,
    passphrase: str,
    salt_override: str | None = None,
) -> str:
    """加密工具：输入明文 key + 口令 → 输出 ``ENC:<token>``。

    本函数仅供 ``backend/scripts/encrypt_ai_key.py`` 使用；
    生产业务代码绝不允许调用此函数（它会接触明文）。
    """
    if not plaintext_key:
        raise AIKeyEncryptionError("待加密的明文 API Key 不能为空。")
    if not passphrase:
        raise AIKeyEncryptionError("口令不能为空。")
    if not plaintext_key.startswith("sk-"):
        # DeepSeek / Doubao / Kimi / OpenAI 官方所有 key 均以 sk- 开头
        # 这里做一次友善提醒（非强制硬校验，但建议遵守）
        log.warning(
            "encrypt_api_key: 明文不以 sk- 开头，确认是合法 AI Provider key？"
            "（如为自建网关 key 可忽略）"
        )

    salt = DEFAULT_EW_SALT
    if salt_override:
        salt = salt_override.encode("utf-8")

    fernet_key = _passphrase_to_fernet_key(passphrase=passphrase, salt=salt)
    f = Fernet(fernet_key)
    token_bytes = f.encrypt(plaintext_key.encode("utf-8"))
    return f"{ENC_PREFIX}{token_bytes.decode('ascii')}"


def derive_ew_salt_from_env() -> str | None:
    """读取 salt 环境变量（极少用，大部分情况默认值就好）。"""
    return os.environ.get("EW_AI_SALT")
