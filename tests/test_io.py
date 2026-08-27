"""io 层测试: 凭证加密 + JPEG 输出。

守护禁令第3、4条: per-user 加密、凭证不泄露、文件名防穿越。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.io import credentials as cred_mod
from src.io import to_jpeg

# 测试用主密钥 (只在本测试进程内有效, 不进任何文件)
_MASTER_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _set_master_key(monkeypatch):
    """为每个测试注入测试主密钥。"""
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", _MASTER_KEY)
    yield


def test_credentials_roundtrip():
    """存储 -> 读取: 内容一致, 且加密落盘。"""
    cred_mod.store_credentials("user_a", {"pie_token": "secret_A"})
    loaded = cred_mod.load_credentials("user_a")
    assert loaded == {"pie_token": "secret_A"}


def test_credentials_encrypted_on_disk():
    """落盘文件必须是密文, 不能含明文 token。"""
    cred_mod.store_credentials("user_b", {"pie_token": "PLAINTEXT_SECRET_123"})
    path = cred_mod._SECRETS_DIR / "user_b.enc"
    raw = path.read_bytes()
    # 明文绝不能出现在密文文件里
    assert b"PLAINTEXT_SECRET_123" not in raw


def test_per_user_isolation():
    """两个用户的凭证互不串号。"""
    cred_mod.store_credentials("alice", {"pie_token": "alice_tok"})
    cred_mod.store_credentials("bob", {"pie_token": "bob_tok"})
    assert cred_mod.load_credentials("alice")["pie_token"] == "alice_tok"
    assert cred_mod.load_credentials("bob")["pie_token"] == "bob_tok"
    # alice 读不到 bob 的
    assert cred_mod.load_credentials("alice") != cred_mod.load_credentials("bob")


def test_filename_safety_against_traversal():
    """恶意 user_id (含 ../) 不能逃出 secrets 目录。"""
    cred_mod.store_credentials("../../../etc/evil", {"pie_token": "x"})
    # 文件名被清洗, 落在 secrets/ 内
    files = list(cred_mod._SECRETS_DIR.glob("*.enc"))
    names = [f.name for f in files]
    assert any("evil" in n for n in names)
    # 没有逃逸到上级目录
    assert all(".." not in n for n in names)


def test_has_and_delete_credentials():
    cred_mod.store_credentials("user_c", {"pie_token": "c"})
    assert cred_mod.has_credentials("user_c") is True
    assert cred_mod.has_credentials("nonexistent") is False
    assert cred_mod.delete_credentials("user_c") is True
    assert cred_mod.has_credentials("user_c") is False


def test_missing_master_key_raises(monkeypatch):
    """没主密钥 -> 报错, 不能静默继续 (防明文落盘)。"""
    monkeypatch.delenv("REMOTE_SENSING_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cred_mod.store_credentials("user_d", {"pie_token": "d"})


def _make_real_jpeg(path: Path) -> bytes:
    """用 PIL 生成一张真实小 JPEG, 返回其字节。"""
    import numpy as np
    from PIL import Image

    arr = np.arange(32 * 32, dtype=np.uint8).reshape(32, 32)
    img = Image.fromarray(arr, mode="L")
    import io as _io

    buf = _io.BytesIO()
    img.save(buf, format="JPEG")
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def test_jpeg_passthrough():
    """源已是合法 JPEG -> 直接拷贝, 字节一致。"""
    src = cred_mod._SECRETS_DIR.parent.parent / "cache" / "src_test.jpg"
    src.parent.mkdir(exist_ok=True)
    data = _make_real_jpeg(src)
    out = to_jpeg(str(src), task_type="vegetation", out_name="out_passthrough")
    assert Path(out).read_bytes() == data
    src.unlink()


def test_jpeg_magic_rejects_fake_bytes():
    """占位/损坏字节伪装 .jpg -> 魔数校验拒绝, 不允许以假乱真。"""
    src = cred_mod._SECRETS_DIR.parent.parent / "cache" / "src_fake.jpg"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"MOCK_JPEG_PLACEHOLDER")
    with pytest.raises(ValueError):
        to_jpeg(str(src), task_type="vegetation", out_name="out_fake")
    src.unlink()
