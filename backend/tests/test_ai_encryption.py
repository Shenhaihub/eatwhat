"""P5-03B AI API Key 多层加密框架测试。

测试点：
  1. 明文 sk-... → encrypt → ENC:xxx → resolve → 还原出同一 sk-...（往返一致）
  2. 配置 Settings(ai_api_key="sk-...") 直接抛 ValidationError（防明文入库 fail-fast）
  3. 配置 Settings(ai_api_key="not_enc_or_empty_xxx") 也抛错（非 ENC: 前缀）
  4. 口令错误 / 密文被篡改 → AIKeyEncryptionError，文案不含敏感信息
  5. 空值 ai_api_key="" → resolve 返回 None（不强制配置）
  6. 密文被双重加密 → 解密后仍以 ENC: 开头，抛错阻止
  7. secret_values 中包含 ew_ai_key_passphrase（脱敏防泄漏）
  8. 加密脚本 encrypt_api_key_for_env 与 resolve_encrypted_api_key 配对兼容
"""
from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.encryption import (
    ENC_PREFIX,
    AIKeyEncryptionError,
    encrypt_api_key_for_env,
    resolve_encrypted_api_key,
)


def test_roundtrip_encrypt_decrypt_matches():
    plain = "sk-deepseek-test-1234567890abcdef"
    passphrase = "test-passphrase-long-enough-123"
    cipher = encrypt_api_key_for_env(plaintext_key=plain, passphrase=passphrase)
    assert cipher.startswith(ENC_PREFIX)
    assert len(cipher) > len(ENC_PREFIX) + 10
    got = resolve_encrypted_api_key(
        encrypted_value=cipher, passphrase=passphrase
    )
    assert got == plain


def test_settings_rejects_plaintext_sk_prefix():
    with pytest.raises(ValidationError, match=r"疑似明文|sk- 前缀"):
        Settings(ai_api_key="sk-plain-leak-test-123")


def test_settings_rejects_random_string_without_enc_prefix():
    with pytest.raises(ValidationError, match=r"以 ENC: 开头|配置错误"):
        Settings(ai_api_key="just-some-random-junk-123")


def test_settings_accepts_empty_ai_api_key():
    s = Settings(ai_api_key="")
    assert s.ai_api_key == ""


def test_settings_accepts_enc_prefix_valid_shape():
    """ENC: 前缀但后面 token 不合法（格式不对）不在这里 fail-fast，
    在 resolve 期再判定—— Settings 只做前缀/明文的格式型防御。"""
    s = Settings(ai_api_key="ENC:not-valid-token-shape-but-prefix-ok")
    assert s.ai_api_key.startswith(ENC_PREFIX)


def test_resolve_returns_none_for_empty():
    assert resolve_encrypted_api_key(encrypted_value="", passphrase="x") is None
    assert resolve_encrypted_api_key(encrypted_value="", passphrase=None) is None


def test_resolve_plaintext_suspected_raises():
    with pytest.raises(AIKeyEncryptionError, match=r"疑似明文"):
        resolve_encrypted_api_key(encrypted_value="sk-direct-plain", passphrase="whatever")


def test_resolve_wrong_prefix_raises():
    with pytest.raises(AIKeyEncryptionError, match=r"格式非法"):
        resolve_encrypted_api_key(encrypted_value="HELLO:whatever", passphrase="whatever")


def test_resolve_enc_format_but_no_passphrase():
    s = Settings(ai_api_key="ENC:some-token")
    with pytest.raises(AIKeyEncryptionError, match=r"EW_AI_KEY_PASSPHRASE 为空"):
        resolve_encrypted_api_key(encrypted_value=s.ai_api_key, passphrase=None)
    with pytest.raises(AIKeyEncryptionError, match=r"EW_AI_KEY_PASSPHRASE 为空"):
        resolve_encrypted_api_key(encrypted_value=s.ai_api_key, passphrase="")


def test_resolve_wrong_passphrase_returns_generic_error():
    """错误口令或密文被篡改都返回相同的通用文案（不做 Oracle 区分）。"""
    cipher = encrypt_api_key_for_env(
        plaintext_key="sk-12345", passphrase="correct-horse-battery-staple-x"
    )
    with pytest.raises(AIKeyEncryptionError, match=r"解密失败.*口令不对或密文损坏"):
        resolve_encrypted_api_key(encrypted_value=cipher, passphrase="wrong-passphrase")

    # 密文被篡改（最后一位替换）
    tampered = cipher[:-1] + ("A" if cipher[-1] != "A" else "B")
    with pytest.raises(AIKeyEncryptionError, match=r"解密失败.*口令不对或密文损坏"):
        resolve_encrypted_api_key(encrypted_value=tampered, passphrase="correct-horse-battery-staple-x")


def test_resolve_error_message_contains_no_secrets():
    """任何 AIKeyEncryptionError 的 args[0] 都不能含 sk-、口令或 salt 片段。"""
    bad_case_msgs: list[str] = []
    try:
        resolve_encrypted_api_key(encrypted_value="sk-leak", passphrase="pw1234567890")
    except AIKeyEncryptionError as e:
        bad_case_msgs.append(str(e))

    try:
        cipher = encrypt_api_key_for_env(plaintext_key="sk-abc", passphrase="pw-good-long-12345")
        resolve_encrypted_api_key(encrypted_value=cipher, passphrase="pw-wrong-long-12345")
    except AIKeyEncryptionError as e:
        bad_case_msgs.append(str(e))

    try:
        Settings(ai_api_key="sk-direct-leak-xyz")
    except ValidationError as e:
        bad_case_msgs.append(str(e))

    for msg in bad_case_msgs:
        # 错误里不能出现完整 sk- 开头的 key 样例
        assert not re.search(r"sk-[A-Za-z0-9]{8,}", msg), f"错误消息疑似泄漏 key: {msg}"
        # 不能出现我们刚刚用的明文口令片段（pw-good-long / pw-wrong-long）
        assert "pw-good-long" not in msg
        assert "pw-wrong-long" not in msg


def test_double_encryption_detected():
    """用户不小心把密文再次加密一遍（ENC:... 加密还会再产生 ENC:xxx）
    → 解密第一层出来的是 ENC:xxx，必须明确抛错提醒。"""
    passphrase = "pw-long-enough-for-pbkdf2-test"
    inner = encrypt_api_key_for_env(plaintext_key="sk-realkey-x", passphrase=passphrase)
    assert inner.startswith(ENC_PREFIX)
    # 再加密一次（模拟用户手滑重复加密）
    double = encrypt_api_key_for_env(plaintext_key=inner, passphrase=passphrase)
    assert double.startswith(ENC_PREFIX)
    with pytest.raises(AIKeyEncryptionError, match=r"重复加密|重新只加密一次"):
        resolve_encrypted_api_key(encrypted_value=double, passphrase=passphrase)


def test_secret_values_includes_passphrase_and_salt():
    s = Settings(
        ai_api_key="ENC:foo",
        ew_ai_key_passphrase="my-secret-pass-1234",
        ew_ai_salt="my-salt-override",
    )
    secrets = s.secret_values
    assert "ew_ai_key_passphrase" in secrets
    assert secrets["ew_ai_key_passphrase"] == "my-secret-pass-1234"
    assert "ew_ai_salt" in secrets
    assert secrets["ew_ai_salt"] == "my-salt-override"


def test_encrypt_requires_min_12_passphrase_script_side():
    """加密工具 encrypt_api_key_for_env 本身不强校验口令长度（让脚本层校验），
    但至少要保证空口令直接报错。"""
    with pytest.raises(AIKeyEncryptionError, match=r"口令不能为空"):
        encrypt_api_key_for_env(plaintext_key="sk-1", passphrase="")
    with pytest.raises(AIKeyEncryptionError, match=r"明文 API Key 不能为空"):
        encrypt_api_key_for_env(plaintext_key="", passphrase="pw-long-enough-1234")


def test_same_input_produces_different_ciphertext_each_time():
    """Fernet 每次都会生成随机 IV，所以同一明文+口令每次加密结果必须不同。"""
    plain = "sk-determinism-check-12345"
    pw = "very-long-passphrase-x-y-z-1"
    c1 = encrypt_api_key_for_env(plaintext_key=plain, passphrase=pw)
    c2 = encrypt_api_key_for_env(plaintext_key=plain, passphrase=pw)
    assert c1 != c2
    # 但两者都能解密回原值
    assert resolve_encrypted_api_key(encrypted_value=c1, passphrase=pw) == plain
    assert resolve_encrypted_api_key(encrypted_value=c2, passphrase=pw) == plain
