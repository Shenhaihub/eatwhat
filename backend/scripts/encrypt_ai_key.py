"""加密 DeepSeek/其他 AI Provider 的明文 API Key 为 ENC:xxx 格式。

运行方式（在 backend 目录下）：
    uv run python scripts/encrypt_ai_key.py

交互流程：
    1. 输入口令（EW_AI_KEY_PASSPHRASE，至少 12 字符，建议密码管理器生成 32+ 随机）
    2. 粘贴明文 sk-... API Key（不显示，关闭 echo）
    3. 工具把 ``ENC:<fernet-token>`` 输出到 stdout

安全规则：
    - 口令只存在内存；
    - 明文 key 只存在内存；
    - 本脚本不写任何文件，不写入 .env，不写入 stdout/stderr 明文；
    - 输出只包含 ENC: 开头的密文，你需要手工拷贝粘贴进 .env。
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

# 保证无论从 backend/ 根目录还是 project/ 根目录运行都能 import 到 app.core
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.encryption import (
    AIKeyEncryptionError,
    encrypt_api_key_for_env,
)


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes", "1", "true", "是"}


def main() -> int:
    print("=" * 64)
    print("EatWhat AI API Key 加密工具")
    print("=" * 64)
    print()
    print("输出格式：ENC:<Fernet-Token>")
    print("你需要把整行（含 ENC: 前缀）复制粘贴进 .env 的 AI_API_KEY= 后面")
    print("同时在 .env 填：EW_AI_KEY_PASSPHRASE=<与你刚才输入一致的口令>")
    print()
    print("重要提示：")
    print("  - 口令建议使用密码管理器生成 32+ 随机字符（字母/数字/符号混合）")
    print("  - 千万不要与你的 DeepSeek key、任何邮箱/账号密码复用")
    print("  - 本工具不读取也不修改 .env，一切靠你手工粘贴")
    print()

    if not _confirm("我已理解并准备继续？[y/N] "):
        print("已取消。")
        return 0

    pass1 = getpass.getpass("1) 输入 EW_AI_KEY_PASSPHRASE（口令，不显示）: ")
    if len(pass1) < 12:
        print("错误：口令长度 < 12 字符，为了安全请重新生成更复杂的口令。", file=sys.stderr)
        return 2
    pass2 = getpass.getpass("   再输一次 EW_AI_KEY_PASSPHRASE（确认）: ")
    if pass1 != pass2:
        print("错误：两次输入的口令不一致。", file=sys.stderr)
        return 2

    plain = getpass.getpass("2) 粘贴明文 sk-... API Key（不显示）: ")
    if not plain.strip():
        print("错误：API Key 为空。", file=sys.stderr)
        return 2
    plain = plain.strip()
    plain2 = getpass.getpass("   再输一次 API Key（确认）: ").strip()
    if plain != plain2:
        print("错误：两次输入的 API Key 不一致。", file=sys.stderr)
        return 2

    try:
        cipher = encrypt_api_key_for_env(plaintext_key=plain, passphrase=pass1)
    except AIKeyEncryptionError as e:
        print(f"加密失败：{e}", file=sys.stderr)
        return 1
    finally:
        # 尽快擦除内存敏感变量（尽力而为；CPython 字符串不可变真正清理困难）
        del plain
        del plain2
        del pass1
        del pass2

    print()
    print("———————————————— 拷贝下方这一整行（含 ENC: 前缀）————————————————")
    print(cipher)
    print("———————————————————————————— 结束 ————————————————————————————")
    print()
    print("之后在 .env 里填两项：")
    print("  AI_API_KEY=<上面拷贝的 ENC:...>")
    print("  EW_AI_KEY_PASSPHRASE=<你的口令>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
