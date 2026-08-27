"""auth.py 访问令牌单元测试 (T-1)。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.io import auth as auth_mod


@pytest.fixture(autouse=True)
def _master_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(auth_mod, "_TOKENS_DIR", tmp_path / "toks")


def test_issue_and_verify_roundtrip():
    token = auth_mod.issue_access_token("alice")
    assert token  # 一次性明文
    assert auth_mod.verify_access_token("alice", token) is True


def test_wrong_token_rejected():
    auth_mod.issue_access_token("alice")
    assert auth_mod.verify_access_token("alice", "forged-token") is False


def test_cross_user_token_rejected():
    """alice 的令牌不能当 bob 用 (归属校验的地基)。"""
    alice_tok = auth_mod.issue_access_token("alice")
    assert auth_mod.verify_access_token("bob", alice_tok) is False


def test_no_token_or_missing_user():
    assert auth_mod.verify_access_token("alice", "") is False
    assert auth_mod.verify_access_token("nobody", "whatever") is False


def test_token_encrypted_on_disk():
    """令牌密文落盘, 明文绝不出现在文件里。"""
    token = auth_mod.issue_access_token("alice")
    raw = (auth_mod._TOKENS_DIR / "alice.tok").read_bytes()
    assert token.encode() not in raw  # Fernet 加密


def test_delete_and_has():
    assert auth_mod.has_access_token("alice") is False
    auth_mod.issue_access_token("alice")
    assert auth_mod.has_access_token("alice") is True
    assert auth_mod.delete_access_token("alice") is True
    assert auth_mod.has_access_token("alice") is False


def test_extract_bearer():
    assert auth_mod.extract_bearer("Bearer abc123") == "abc123"
    assert auth_mod.extract_bearer("bearer abc") == "abc"  # scheme 大小写不敏感
    assert auth_mod.extract_bearer("Basic xyz") == ""      # 非 Bearer 拒绝
    assert auth_mod.extract_bearer("") == ""
