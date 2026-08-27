"""per-user 访问令牌 (蓝图盲区#1 的最小修复)。

问题: user_id 客户端自报, 任何人可用他人 user_id 读/覆盖凭证、查任务、
耗配额。本模块给每个用户发一次性展示的访问令牌:

  - POST /credentials 首次绑定时签发, 响应里返回一次 (不再可查)
  - 之后 /analyze 与 /tasks/* 请求带 Authorization: Bearer {token}
  - 令牌与凭证同密级: Fernet 加密落 cache/secrets/, 主密钥同环境变量

已知限制 (MVP 接受, 记录在案): 首次绑定仍可被抢注 (受害者还没绑定时
攻击者先绑); 完整解法需带外注册流程, 属后期。已绑定后不可无 token 覆盖。
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import secrets as _secrets
from pathlib import Path

from src.io.credentials import _get_fernet, _safe

# 令牌密文存放目录 (与凭证同目录, 同 gitignore 策略)
_TOKENS_DIR = paths_cache_root() / "secrets"


def issue_access_token(user_id: str) -> str:
    """签发访问令牌: 随机生成 -> 加密落盘 -> 返回明文 (仅此一次)。"""
    token = _secrets.token_urlsafe(24)
    f = _get_fernet()
    _TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    path = _TOKENS_DIR / f"{_safe(user_id)}.tok"
    path.write_bytes(f.encrypt(token.encode()))
    return token


def verify_access_token(user_id: str, token: str) -> bool:
    """校验令牌。恒定时间比较防时序侧信道; 任何异常都判不过。"""
    if not token:
        return False
    path = _TOKENS_DIR / f"{_safe(user_id)}.tok"
    if not path.exists():
        return False
    try:
        stored = _get_fernet().decrypt(path.read_bytes()).decode()
    except Exception:
        return False
    # compare_digest 对含非 ASCII 的 str 抛 TypeError —— 编码后比较, 畸形 Bearer 判 401 而非 500 (G3 审查)
    return _secrets.compare_digest(stored.encode(), token.encode("utf-8", errors="replace"))


def get_access_token(user_id: str) -> str | None:
    """解密取回现有令牌 (本地多账号切换用; 不重签 —— 重签会使旧令牌失效)。"""
    path = _TOKENS_DIR / f"{_safe(user_id)}.tok"
    if not path.exists():
        return None
    try:
        return _get_fernet().decrypt(path.read_bytes()).decode()
    except Exception:
        return None


def has_access_token(user_id: str) -> bool:
    """该用户是否已签发过令牌 (= 是否已完成首次绑定)。"""
    return (_TOKENS_DIR / f"{_safe(user_id)}.tok").exists()


def delete_access_token(user_id: str) -> bool:
    """删除令牌 (测试清理 / 用户解绑用)。"""
    path = _TOKENS_DIR / f"{_safe(user_id)}.tok"
    if path.exists():
        path.unlink()
        return True
    return False


def extract_bearer(authorization: str) -> str:
    """从 Authorization 头提取 Bearer 令牌, 格式不对返回空串。"""
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()
