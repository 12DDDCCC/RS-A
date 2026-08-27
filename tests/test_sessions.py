"""E-1 会话层 + E-3 SSE 测试: 多轮记忆 / 事件溯源 / 崩溃恢复 / 流式。"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.runtime.sessions import SessionStore


@pytest.fixture
def ss(tmp_path):
    return SessionStore(db_path=tmp_path / "jobs.db")


# ---------- 会话与多轮记忆 ----------

def test_session_create_and_get(ss):
    sid = ss.create_session("u1")
    rec = ss.get_session(sid)
    assert rec["user_id"] == "u1"


def test_latest_session(ss):
    ss.create_session("u1")
    s2 = ss.create_session("u1")
    ss.create_session("other")
    assert ss.latest_session("u1") == s2  # 最近
    assert ss.latest_session("nobody") is None


def test_message_history_roundtrip(ss):
    sid = ss.create_session("u1")
    ss.add_message(sid, "user", "看看北京植被")
    ss.add_message(sid, "assistant", "分析区域：北京…")
    hist = ss.history(sid)
    assert len(hist) == 2
    assert hist[0]["role"] == "user" and hist[1]["role"] == "assistant"
    assert hist[0]["content"] == "看看北京植被"


def test_history_limit_keeps_latest(ss):
    sid = ss.create_session("u1")
    for i in range(10):
        ss.add_message(sid, "user", f"msg{i}")
    hist = ss.history(sid, limit=3)
    assert [h["content"] for h in hist] == ["msg7", "msg8", "msg9"]


# ---------- 事件溯源日志 ----------

def test_events_seq_monotonic(ss):
    ss.emit("t1", "stage", "开始")
    ss.emit("t1", "stage", "进行中", {"attempt": 2})
    ss.emit("t1", "usage", "llm", {"input_tokens": 100})
    evs = ss.events_after("t1")
    assert [e["seq"] for e in evs] == [1, 2, 3]
    assert evs[1]["payload"] == {"attempt": 2}
    assert evs[2]["kind"] == "usage"


def test_events_after_incremental(ss):
    ss.emit("t1", "stage", "a")
    ss.emit("t1", "stage", "b")
    ss.emit("t1", "stage", "c")
    tail = ss.events_after("t1", after_seq=1)
    assert [e["detail"] for e in tail] == ["b", "c"]


def test_events_isolated_between_tasks(ss):
    ss.emit("tA", "stage", "A1")
    ss.emit("tB", "stage", "B1")
    assert len(ss.events_after("tA")) == 1
    assert ss.events_after("tA")[0]["detail"] == "A1"


def test_recover_interrupted(ss):
    ss.recover_interrupted(["t1", "t2"])
    ev = ss.events_after("t1")
    assert ev[0]["kind"] == "system"
    assert ev[0]["payload"]["recovered"] is True


# ---------- API 集成: session 传递 + SSE + 进度事件 ----------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    from src.io import auth as auth_mod, credentials as cred_mod

    for uid in ("eu1",):
        cred_mod.delete_credentials(uid)
        auth_mod.delete_access_token(uid)
    from src.main import app

    return TestClient(app)


@pytest.fixture
def with_creds(client):
    r = client.post("/credentials", json={"user_id": "eu1", "credentials": {"pie_token": "t"}})
    return client, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mock_cb(task_id=None) -> dict:
    return {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"北京植被","need_clarify":false,"clarify_question":""}',
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\nok",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }


def test_analyze_returns_session_and_events(client, with_creds, monkeypatch):
    """202 响应带 session_id; 任务事件日志有阶段事件; SSE 可拉取。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_cb)
    r = client.post("/analyze", json={
        "user_input": "北京植被变化", "user_id": "eu1", "place": "北京",
    }, headers=headers)
    assert r.status_code == 202
    body = r.json()
    task_id, sid = body["task_id"], body["session_id"]
    assert sid

    # 事件日志有进度 (排队 + 完成)
    from src.runtime.sessions import sessions as ss
    evs = ss.events_after(task_id)
    kinds = [e["kind"] for e in evs]
    assert "stage" in kinds
    assert any(e["payload"].get("status") == "done" for e in evs)

    # 消息落会话 (user + assistant)
    hist = ss.history(sid)
    assert hist[0]["role"] == "user" and "北京" in hist[0]["content"]
    assert hist[-1]["role"] == "assistant"

    # SSE: after=0 应能拿到事件流并正常 close (TestClient 同步完成, 直接读)
    with client.stream("GET", f"/tasks/{task_id}/events", headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = []
        for line in resp.iter_lines():
            lines.append(line)
    text = "\n".join(lines)
    assert "event: progress" in text
    assert "event: close" in text


def test_analyze_continues_session(client, with_creds, monkeypatch):
    """带 session_id 再提问 -> 同一会话累积消息 (多轮记忆的根)。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_cb)
    r1 = client.post("/analyze", json={
        "user_input": "北京植被", "user_id": "eu1", "place": "北京",
    }, headers=headers)
    sid = r1.json()["session_id"]
    # per-user 单飞: 第一单已 done, 第二单可提交
    r2 = client.post("/analyze", json={
        "user_input": "换成上海的", "user_id": "eu1", "place": "上海",
        "session_id": sid,
    }, headers=headers)
    assert r2.status_code == 202
    assert r2.json()["session_id"] == sid  # 续会话

    from src.runtime.sessions import sessions as ss
    hist = ss.history(sid)
    assert hist[0]["content"] == "北京植被"
    assert hist[2]["content"] == "换成上海的"


def test_invalid_session_degrades_to_new(client, with_creds, monkeypatch):
    """无效/他人 session_id -> 静默新开 (不报错不越权)。"""
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_cb)
    client.post("/analyze", json={
        "user_input": "北京植被", "user_id": "eu1", "place": "北京",
    }, headers=headers)
    r = client.post("/analyze", json={
        "user_input": "上海水体", "user_id": "eu1", "place": "上海",
        "session_id": "not-exist",
    }, headers=headers)
    assert r.status_code == 202
    assert r.json()["session_id"] != "not-exist"


def test_latest_session_endpoint(client, with_creds, monkeypatch):
    import src.main as main_mod

    client, headers = with_creds
    monkeypatch.setattr(main_mod, "_make_callbacks", _mock_cb)
    r = client.post("/analyze", json={
        "user_input": "北京植被", "user_id": "eu1", "place": "北京",
    }, headers=headers)
    sid = r.json()["session_id"]
    s = client.get("/sessions/latest?user_id=eu1", headers=headers)
    assert s.status_code == 200
    assert s.json()["session_id"] == sid
    assert len(s.json()["messages"]) >= 1
