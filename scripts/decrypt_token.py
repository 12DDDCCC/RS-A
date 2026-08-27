# -*- coding: utf-8 -*-
"""解密 per-user 访问令牌并打印到 stdout (B2-fast: dsh 挂接用)。

用途: sidecar 启动脚本以 for /f 捕获 stdout 注入 REMOTE_SENSING_TOKEN 环境变量,
令牌不落第二份盘、不出现在命令行参数与日志中。

用法:
  .venv/Scripts/python.exe RS-agent/scripts/decrypt_token.py [user_id]
  (user_id 缺省 rs-a-user)

安全: 仅本地使用; 输出经启动脚本捕获, 请勿重定向到文件或回显。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

# 主密钥在本机 .env (与 src/main.py 同款加载语义; 测试可用 NO_DOTENV=1 禁读)
from dotenv import load_dotenv

if os.environ.get("REMOTE_SENSING_NO_DOTENV") != "1":
    load_dotenv(PROJECT / ".env")

from src.io.credentials import _get_fernet  # noqa: E402
from src.io.auth import _TOKENS_DIR, _safe  # noqa: E402


def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else "rs-a-user"
    path = _TOKENS_DIR / f"{_safe(user_id)}.tok"
    if not path.exists():
        print(f"ERROR: 用户 {user_id} 尚未绑定凭证 (无 {path.name})", file=sys.stderr)
        sys.exit(1)
    try:
        token = _get_fernet().decrypt(path.read_bytes()).decode()
    except Exception as e:
        print(f"ERROR: 解密失败 ({e}) — 检查 REMOTE_SENSING_MASTER_KEY", file=sys.stderr)
        sys.exit(2)
    print(token)


if __name__ == "__main__":
    main()
