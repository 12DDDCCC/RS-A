"""per-user 凭证加密管理。

禁令第3、4条的硬护栏:
  - per-user 加密存储 (每个用户独立文件, 非全局 env, 防多用户串号)
  - 凭证绝不进 git (存 cache/secrets/, 已在 .gitignore)
  - 凭证绝不落日志

加密用 Fernet (对称加密), 主密钥来自环境变量 REMOTE_SENSING_MASTER_KEY。
MVP 用单一主密钥; 生产环境应接 KMS。
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

# 凭证存放目录 (项目内, 已 gitignore)
_SECRETS_DIR = paths_cache_root() / "secrets"

_MASTER_KEY_ENV = "REMOTE_SENSING_MASTER_KEY"


def _get_fernet() -> Fernet:
    """从环境变量取主密钥, 构造 Fernet。

    密钥不在代码里, 不进 git, 不落日志。
    """
    key = os.environ.get(_MASTER_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"未设置主密钥环境变量 {_MASTER_KEY_ENV}。"
            f"请用 Fernet.generate_key() 生成并配置到环境变量。"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def store_credentials(user_id: str, credentials: dict) -> Path:
    """加密存储某用户的凭证。

    Args:
        user_id: 用户唯一标识 (做文件名, 不含凭证)。
        credentials: 平台凭证 dict (如 {"pie_token": "..."} )。

    Returns:
        加密文件路径 (用户无感, 已在 secrets/ 下)。
    """
    f = _get_fernet()
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(credentials, ensure_ascii=False).encode()
    encrypted = f.encrypt(payload)
    path = _SECRETS_DIR / f"{_safe(user_id)}.enc"
    path.write_bytes(encrypted)
    return path


def load_credentials(user_id: str) -> dict:
    """解密读取某用户的凭证。

    返回的 dict 仅供本次请求使用, 用完即弃, 不缓存到全局。
    """
    f = _get_fernet()
    path = _SECRETS_DIR / f"{_safe(user_id)}.enc"
    if not path.exists():
        raise FileNotFoundError(f"用户 {user_id} 无已存储凭证")
    decrypted = f.decrypt(path.read_bytes())
    return json.loads(decrypted)


def has_credentials(user_id: str) -> bool:
    """检查某用户是否已存凭证 (不读取内容)。"""
    return (_SECRETS_DIR / f"{_safe(user_id)}.enc").exists()


def delete_credentials(user_id: str) -> bool:
    """删除某用户凭证。"""
    path = _SECRETS_DIR / f"{_safe(user_id)}.enc"
    if path.exists():
        path.unlink()
        return True
    return False


def _safe(user_id: str) -> str:
    """把 user_id 转成安全的文件名 (防路径穿越)。"""
    import re

    return re.sub(r"[^a-zA-Z0-9_-]", "_", user_id)
