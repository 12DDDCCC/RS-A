"""P1-4 计划节点测试: 确定性三查 + graph 接线。"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.agent.planning import validate_plan
from src.io import credentials as cred_mod

TEST_USER = "plan_user"


@pytest.fixture(autouse=True)
def _seed_creds(monkeypatch):
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    cred_mod.store_credentials(TEST_USER, {"pie_token": "test"})
    yield


# ---------- validate_plan 确定性三查 ----------

def test_valid_plan_passes():
    r = validate_plan({
        "dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
        "index": "NDVI", "bands": ["B8", "B4"],
        "years": [2020, 2024], "method": "夏季中值合成",
    })
    assert r.passed, r.issues


def test_hallucinated_dataset_rejected():
    r = validate_plan({"dataset_id": "FAKE/DS", "bands": [], "years": [2020]})
    assert not r.passed
    assert any("白名单" in i for i in r.issues)


def test_time_window_before_coverage_rejected():
    """2015 起的时序选 S2_SR (2017-03-28 才有数据) -> 拦截 (静默空结果防线)。"""
    r = validate_plan({
        "dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
        "index": "NDVI", "bands": ["B8", "B4"], "years": [2015, 2024],
    })
    assert not r.passed
    assert any("无数据" in i for i in r.issues)


def test_landsat9_2022_plan_ok_but_2013_rejected():
    """LC09 2021-10 起: 2022 计划过, 2013 计划被拦。"""
    ok = validate_plan({
        "dataset_id": "LANDSAT/LC09/C02/T1_L2",
        "bands": ["SR_B5", "SR_B4"], "years": [2022, 2024],
    })
    assert ok.passed
    bad = validate_plan({
        "dataset_id": "LANDSAT/LC09/C02/T1_L2",
        "bands": ["SR_B5", "SR_B4"], "years": [2013, 2024],
    })
    assert not bad.passed


def test_band_not_in_dataset_rejected():
    r = validate_plan({
        "dataset_id": "LANDSAT/LC09/C02/T1_L2",
        "bands": ["B8", "B4"], "years": [2022],
    })
    assert not r.passed
    assert any("错配" in i for i in r.issues)


# ---------- graph 接线 ----------

def _cb(plan_json: str) -> dict:
    return {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"植被","need_clarify":false,"clarify_question":""}',
        "plan": lambda p: plan_json,
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\nok",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }


def test_graph_with_good_plan_produces_jpeg():
    from src.agent.graph import build_graph

    final = build_graph().invoke({
        "user_input": "植被", "user_id": TEST_USER,
        "region": {"lon_min": 116, "lat_min": 39, "lon_max": 117, "lat_max": 40},
        "llm_callbacks": _cb('{"dataset_id":"COPERNICUS/S2_SR_HARMONIZED","index":"NDVI","bands":["B8","B4"],"years":[2020,2024],"method":"中值合成"}'),
        "retry_count": 0, "max_retries": 2,
    })
    assert final.get("analysis_plan", {}).get("dataset_id") == "COPERNICUS/S2_SR_HARMONIZED"
    assert final.get("final_output", "").endswith(".jpg")


def test_graph_with_bad_plan_fails_before_generate():
    """计划选错数据集 -> 生成前拦截 (generate 回调不应被调用)。"""
    from src.agent.graph import build_graph

    calls = {"generate": 0}

    def gen(p):
        calls["generate"] += 1
        return "x = 1"

    cb = _cb('{"dataset_id":"FAKE/DS","bands":[],"years":[2020]}')
    cb["generate"] = gen
    final = build_graph().invoke({
        "user_input": "植被", "user_id": TEST_USER,
        "region": {"lon_min": 116, "lat_min": 39, "lon_max": 117, "lat_max": 40},
        "llm_callbacks": cb, "retry_count": 0, "max_retries": 2,
    })
    assert final.get("error")
    assert "分析计划未通过校验" in final["error"]
    assert calls["generate"] == 0  # 前移拦截, 不烧生成


def test_graph_without_plan_callback_passthrough():
    """无 plan 回调 -> 直通不阻断 (兼容现有 mock 与生产空回调)。"""
    from src.agent.graph import build_graph

    final = build_graph().invoke({
        "user_input": "植被", "user_id": TEST_USER,
        "region": {"lon_min": 116, "lat_min": 39, "lon_max": 117, "lat_max": 40},
        "llm_callbacks": {
            "clarify": lambda p: '{"task_type":"vegetation","clarified":"植被","need_clarify":false,"clarify_question":""}',
            "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
            "review": lambda p: "APPROVED\nok",
            "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
        },
        "retry_count": 0, "max_retries": 2,
    })
    assert final.get("final_output", "").endswith(".jpg")


def test_landsat8_long_timespan_allowed():
    """LC08 (备用 ID) 用自己的时间覆盖: 2014 起的长时序放行。

    (回归: 曾误套 LC09 的 2021 起点, 冤杀所有 2013-2020 合法长时序计划。)
    """
    r = validate_plan({
        "dataset_id": "LANDSAT/LC08/C02/T1_L2",
        "bands": ["SR_B5", "SR_B4"], "years": [2014, 2024],
    })
    assert r.passed, r.issues
