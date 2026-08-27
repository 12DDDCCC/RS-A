"""JobStore 任务生命周期测试: 202 入队 / 澄清挂起续跑 / 序列化剥离。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.runtime import jobs as jobs_mod
from src.runtime.jobs import JobStore, serialize_state, submit_answer

REGION = {"lon_min": 116.0, "lat_min": 39.0, "lon_max": 117.0, "lat_max": 40.0}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """独立 sqlite + 主密钥, 不污染 cache/jobs.db。"""
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    s = JobStore(db_path=tmp_path / "jobs.db")
    return s


def _ok_callbacks() -> dict:
    return {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"北京植被","need_clarify":false,"clarify_question":""}',
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\nok",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }


def _seed_creds(user_id: str):
    from src.io import store_credentials
    store_credentials(user_id, {"pie_token": "test"})


def test_serialize_strips_callbacks_and_credentials():
    state = {
        "user_input": "北京植被",
        "credentials": {"pie_token": "SECRET"},
        "llm_callbacks": {"generate": lambda p: "x"},
        "region": REGION,
    }
    raw = serialize_state(state)
    assert "SECRET" not in raw  # 凭证绝不入库
    assert "llm_callbacks" not in raw  # callable 不可 json 化, 必须剥离
    parsed = json.loads(raw)
    assert parsed["region"] == REGION


def test_job_lifecycle_done(store, monkeypatch):
    monkeypatch.setattr(jobs_mod, "store", store)
    _seed_creds("u1")
    task_id = store.create("u1", {
        "user_input": "北京植被", "region": REGION,
        "user_id": "u1", "retry_count": 0, "max_retries": 2,
    })
    assert store.get(task_id)["status"] == "queued"

    jobs_mod.run_job(task_id, _ok_callbacks())
    rec = store.get(task_id)
    assert rec["status"] == "done"
    state = json.loads(rec["state_json"])
    assert state.get("result_jpeg", "").endswith(".jpg")


def test_job_fails_without_generate_callback(store, monkeypatch):
    monkeypatch.setattr(jobs_mod, "store", store)
    _seed_creds("u2")
    task_id = store.create("u2", {
        "user_input": "北京植被", "region": REGION,
        "user_id": "u2", "retry_count": 0, "max_retries": 2,
    })
    jobs_mod.run_job(task_id, callbacks={})  # 未配 LLM
    rec = store.get(task_id)
    assert rec["status"] == "failed"
    assert rec["error_code"] == "LLM_NOT_CONFIGURED"


def test_need_clarify_suspend_then_answer(store, monkeypatch):
    monkeypatch.setattr(jobs_mod, "store", store)
    _seed_creds("u3")

    cb = _ok_callbacks()
    cb["clarify"] = lambda p: '{"task_type":"vegetation","clarified":"","need_clarify":true,"clarify_question":"要看哪个时间段?"}'

    task_id = store.create("u3", {
        "user_input": "看看变化", "region": REGION,
        "user_id": "u3", "retry_count": 0, "max_retries": 2,
    })
    jobs_mod.run_job(task_id, cb)
    assert store.get(task_id)["status"] == "need_clarify"

    # 续跑: 回答合入 user_input, 用正常回调 (不再反问)
    ok = submit_answer(task_id, "近三年的植被变化", _ok_callbacks(), runner=jobs_mod.run_job)
    assert ok
    rec = store.get(task_id)
    assert rec["status"] == "done"
    state = json.loads(rec["state_json"])
    assert state["user_input"] == "近三年的植被变化"


def test_answer_rejects_wrong_status(store, monkeypatch):
    monkeypatch.setattr(jobs_mod, "store", store)
    _seed_creds("u4")
    task_id = store.create("u4", {
        "user_input": "北京植被", "region": REGION,
        "user_id": "u4", "retry_count": 0, "max_retries": 2,
    })
    # queued 状态不能 answer
    assert submit_answer(task_id, "x", {}, runner=jobs_mod.run_job) is False


def test_per_user_single_flight(store):
    store.create("u5", {"user_input": "a", "user_id": "u5"})
    assert store.has_running("u5") is not None
    assert store.has_running("nobody") is None
