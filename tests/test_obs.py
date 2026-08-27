"""E 续: 结构化日志 + 脱敏 + prompt 版本化测试。"""
from __future__ import annotations

import json

import pytest

from src.runtime import obs
from src.runtime.obs import _scrub, log_event, recent_errors


@pytest.fixture(autouse=True)
def _iso_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "LOGS_DIR", tmp_path / "logs")


def test_prompt_version_defined():
    """提示词版本常量存在且格式合法 (评测归因依赖)。"""
    from src.agent.prompts import PROMPT_VERSION

    major, minor = PROMPT_VERSION.split(".")
    assert major.isdigit() and minor.isdigit()


def test_log_event_writes_jsonl():
    log_event("stage", task_id="t1", stage="正在编写分析代码")
    log_event("error", task_id="t1", error_code="CODEGEN_FAILED")
    files = list(obs.LOGS_DIR.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    recs = [json.loads(l) for l in lines]
    assert recs[0]["kind"] == "stage" and recs[0]["task_id"] == "t1"
    assert recs[1]["error_code"] == "CODEGEN_FAILED"
    assert all("ts" in r for r in recs)


def test_scrub_masks_tokens_and_keys():
    line = json.dumps({"pie_token": "SECRET_VALUE_123", "note": "Bearer abc123xyz789"})
    scrubbed = _scrub(line)
    assert "SECRET_VALUE_123" not in scrubbed
    assert "abc123xyz789" not in scrubbed
    # 非敏感字段保留
    assert "note" in scrubbed


def test_scrub_masks_openai_style_key():
    assert "sk-abcdefghijklmnop123456" not in _scrub("key=sk-abcdefghijklmnop123456")


def test_recent_errors_filters_kind():
    log_event("stage", task_id="a", stage="ok")
    log_event("error", task_id="b", error_code="X1")
    log_event("error", task_id="c", error_code="X2")
    errs = recent_errors(limit=10)
    assert [e["error_code"] for e in errs] == ["X1", "X2"]


def test_log_event_never_raises(tmp_path, monkeypatch):
    """观测旁路: 日志目录不可写也不抛 (主链路保护)。"""
    monkeypatch.setattr(obs, "LOGS_DIR", tmp_path / "no" / "perm" / "?bad")
    log_event("stage", task_id="t", stage="x")  # 不应抛


def test_node_emit_writes_both_channels(tmp_path):
    """graph 节点 _emit -> 事件日志 + jsonl 双写。"""
    from src.runtime.sessions import SessionStore

    ss = SessionStore(db_path=tmp_path / "jobs.db")
    import src.runtime.sessions as ses_mod

    orig = ses_mod.sessions
    ses_mod.sessions = ss
    try:
        from src.agent.nodes import _emit

        _emit({"task_id": "tk1"}, "stage", "正在理解需求")
        evs = ss.events_after("tk1")
        assert evs[0]["detail"] == "正在理解需求"
    finally:
        ses_mod.sessions = orig
    # jsonl 也写了一条
    assert any("正在理解需求" in l for l in (obs.LOGS_DIR / list(obs.LOGS_DIR.glob("*.jsonl"))[0].name).read_text(encoding="utf-8").splitlines())
