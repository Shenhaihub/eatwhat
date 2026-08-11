"""加密 DeepSeek/其他 AI Provider 的明文 API Key 为 ENC:xxx 格式。

运行方式（在 backend 目录下）：
    uv run python scripts/encrypt_ai_key.py                    # 原交互模式（双二次确认）
    uv run python scripts/encrypt_ai_key.py --auto-generate    # 一键模式：自动生成口令+salt+加密，输出 .env 可用三行

交互流程（默认模式）：
    1. 输入口令（EW_AI_KEY_PASSPHRASE，至少 12 字符，建议密码管理器生成 32+ 随机）
    2. 粘贴明文 sk-... API Key（不显示，关闭 echo）
    3. 工具把 ``ENC:<fernet-token>`` 输出到 stdout

一键模式（--auto-generate / -a）：
    · 只需粘贴 1 次明文 API Key（可选 salt 用随机生成）
    · 自动生成 cryptographically random 的 36 字符 passphrase + 16 字符 url-safe salt
    · 最终以 .env 格式输出 **5 行**（AI_PROVIDER / AI_API_KEY / EW_AI_KEY_PASSPHRASE / EW_AI_SALT
      以及 AI_MODEL 默认值），你可以直接复制粘贴进 .env。

安全规则：
    - 口令/明文 key 只存在内存；脚本结束后不留下任何拷贝（尽力 del 局部变量）
    - 本脚本绝不写 .env，所有输出到 stdout；你需要自己粘贴
    - 输出只包含 ENC: 开头的密文、随机口令/盐；随机口令/盐只会**这次输出一次**，
      丢了无法恢复（需要重新加密）——因此请立即复制进密码管理器保存
"""
from __future__ import annotations

import argparse
import getpass
import secrets
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

DEFAULT_PHRASE_LEN = 36
DEFAULT_SALT_LEN = 16  # 字节 → base64 后大约 22 字符


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes", "1", "true", "是"}


def _random_passphrase(n_chars: int = DEFAULT_PHRASE_LEN) -> str:
    """url-safe 字母数字 + 下划线 + 减号 的 n_chars 长强随机口令。"""
    alphabet = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_"
    )
    return "".join(secrets.choice(alphabet) for _ in range(n_chars))


def _random_salt(n_bytes: int = DEFAULT_SALT_LEN) -> str:
    """加密模块使用"任意字符串"作为 salt，这里给 url-safe base64 的随机串。

    注意：encrypt_api_key_for_env(passphrase=..., salt_override=None) 时会自动生成
    salt；这里 --auto-generate 显式生成并输出，让部署者能把 salt 也保存到密码
    管理器，跨机器/跨进程加密结果保持一致（若你允许每次新 salt，也可直接把
    EW_AI_SALT 留空）。
    """
    import base64

    raw = secrets.token_bytes(n_bytes)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _interactive_mode() -> int:
    print("=" * 64)
    print("EatWhat AI API Key 加密工具（交互模式）")
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
    print("  # 可选：若你想固定 salt（跨机器密文一致），也填 EW_AI_SALT=<随机串>；留空会自动用 PBKDF2 新盐")
    return 0


def _auto_generate_mode(*, keep_salt: bool) -> int:
    print("=" * 64)
    print("EatWhat AI API Key 加密工具（一键生成口令/盐/密文）")
    print("=" * 64)
    print()
    print("流程：")
    print("  1. 粘贴 1 次明文 sk-... API Key")
    print("  2. 脚本自动生成 36 字符强随机口令（EW_AI_KEY_PASSPHRASE）")
    print(f"  3. 自动生成 16 字节 url-safe 随机盐（{'固定使用' if keep_salt else '每次加密自动新生成，不强制输出'}）")
    print("  4. 输出 5 行 .env 模板：AI_PROVIDER / AI_API_KEY / EW_AI_KEY_PASSPHRASE / EW_AI_SALT / AI_MODEL")
    print()
    print(" ⚠️   口令与盐只会输出这一次，请立即复制粘贴到密码管理器保存！")
    print("     若丢失口令/盐，**无法解密**已加密的 AI_API_KEY，需要重新加密。")
    print()
    if not _confirm("我已理解，准备粘贴明文 API Key？[y/N] "):
        print("已取消。")
        return 0

    plain = getpass.getpass("粘贴明文 sk-... API Key（不显示）: ").strip()
    if not plain:
        print("错误：API Key 为空。", file=sys.stderr)
        return 2
    plain_chk = getpass.getpass("再粘贴一次（确认，不显示）: ").strip()
    if plain != plain_chk:
        print("错误：两次输入的 API Key 不一致。", file=sys.stderr)
        return 2

    passphrase = _random_passphrase(DEFAULT_PHRASE_LEN)
    salt = _random_salt(DEFAULT_SALT_LEN) if keep_salt else None

    try:
        cipher = encrypt_api_key_for_env(
            plaintext_key=plain,
            passphrase=passphrase,
            salt_override=salt,
        )
    except AIKeyEncryptionError as e:
        print(f"加密失败：{e}", file=sys.stderr)
        return 1
    finally:
        del plain
        del plain_chk

    print()
    print("=" * 72)
    print("  以下 5 行可直接复制粘贴进 backend/.env：")
    print("=" * 72)
    print("# AI Provider（P5 动态 AI 链路）：mock / deepseek / auto")
    print('AI_PROVIDER=deepseek')
    print(f'AI_API_KEY={cipher}')
    print(f'EW_AI_KEY_PASSPHRASE={passphrase}')
    if keep_salt and salt:
        print(f'EW_AI_SALT={salt}')
    else:
        print("# EW_AI_SALT 留空：每次加密/解密自动用新盐（安全性相同，不可比密文）")
        print("# EW_AI_SALT=")
    print("# 默认模型：DeepSeek V4 Flash（最便宜的 O1 级推理模型）")
    print('AI_MODEL=deepseek-v4-flash')
    print("=" * 72)
    print()
    print("AI 限流（P5-07，日维度；0 = 不限制该维度）：")
    print("AI_DAILY_USER_LIMIT=50   # 单用户每天最多 50 次真实 AI 调用（超了自动切规则引擎）")
    print("AI_GLOBAL_DAILY_LIMIT=5000 # 全服每天最多 5000 次真实 AI 调用（超了自动切规则引擎）")

    # 安全：口令 & 盐已输出到 stdout，内存副本尽快擦
    del passphrase
    if salt:
        del salt
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DeepSeek API Key → ENC:xxx Fernet 加密工具（支持交互/一键生成两种模式）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-a",
        "--auto-generate",
        action="store_true",
        help="一键模式：自动生成强口令（+可选盐），只让你粘贴 1 次明文 Key",
    )
    group.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="（默认）交互模式：你自己提供口令，双二次确认后加密",
    )
    parser.add_argument(
        "--no-fixed-salt",
        action="store_true",
        help="仅 --auto-generate 生效：不输出固定 EW_AI_SALT（每次用 PBKDF2 新盐，安全性相同）",
    )
    args = parser.parse_args()

    if args.auto_generate:
        return _auto_generate_mode(keep_salt=not args.no_fixed_salt)
    # 默认：交互模式（含 -i）
    return _interactive_mode()


if __name__ == "__main__":
    raise SystemExit(main())
