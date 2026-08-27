"""Agent 主链路测试 (LangGraph)。

验证标准 (PLAN 步骤6): mock 平台下跑通"北京 NDVI"示例。
覆盖: 正常出图 / 需澄清反问 / 异常触发纠错重试 / 重试上限防护。

凭证流 (P0-5): state 只带 user_id, 凭证由 fixture 预先加密存储。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.agent.graph import build_graph
from src.io import credentials as cred_mod

REGION = {"lon_min": 116.0, "lat_min": 39.0, "lon_max": 117.0, "lat_max": 40.0}
TEST_USER = "test_agent_user"


@pytest.fixture(autouse=True)
def _seed_creds(monkeypatch):
    """每个测试: 独立主密钥 + 预存 TEST_USER 凭证。"""
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    cred_mod.store_credentials(TEST_USER, {"pie_token": "test"})
    yield


def _good_callbacks() -> dict:
    """一组"顺利通过"的 mock LLM 回调。"""
    return {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"北京近十年植被覆盖变化","need_clarify":false,"clarify_question":"","suggested_methods":["植被指数时序"]}',
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\n代码合理",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }


def test_pipeline_produces_jpeg_for_beijing_ndvi():
    """北京 NDVI 任务: 全程顺利 -> 输出 JPEG 路径。"""
    graph = build_graph()
    final = graph.invoke({
        "user_input": "北京近十年植被覆盖变化",
        "region": REGION,
        "user_id": TEST_USER,
        "llm_callbacks": _good_callbacks(),
        "retry_count": 0,
        "max_retries": 2,
    })
    assert final.get("need_clarify") is False
    assert final.get("final_output")
    assert final["final_output"].endswith(".jpg")


def test_pipeline_asks_clarification_when_intent_unclear():
    """意图不明 (没区域) -> need_clarify, 输出反问。"""
    cb = _good_callbacks()
    cb["clarify"] = lambda p: '{"task_type":"unknown","clarified":"","need_clarify":true,"clarify_question":"请问你要分析哪个区域、什么时间范围的什么现象?"}'
    graph = build_graph()
    final = graph.invoke({
        "user_input": "看看变化",
        "region": {},
        "user_id": TEST_USER,
        "llm_callbacks": cb,
        "max_retries": 2,
    })
    assert final.get("need_clarify") is True
    assert "[需澄清]" in final["final_output"]


def test_pipeline_retries_on_anomalous_then_ok():
    """第一次结果异常 -> 纠错重试 -> 第二次正常 -> 出图。"""
    cb = _good_callbacks()
    diag_calls = {"n": 0}

    def diag(p):
        diag_calls["n"] += 1
        if diag_calls["n"] == 1:
            return '{"diagnosis":"bad","reason":"NDVI为负,疑似波段反","should_retry":true,"retry_hint":"检查波段顺序"}'
        return '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}'

    cb["diagnose"] = diag
    graph = build_graph()
    final = graph.invoke({
        "user_input": "植被变化",
        "region": REGION,
        "user_id": TEST_USER,
        "llm_callbacks": cb,
        "retry_count": 0,
        "max_retries": 2,
    })
    assert final.get("final_output", "").endswith(".jpg")
    assert final.get("retry_count") == 1


def test_pipeline_stops_after_max_retries():
    """始终异常 -> 达上限 -> 停止重试, 仍输出(诊断器最后一次判定)。"""
    cb = _good_callbacks()
    cb["diagnose"] = lambda p: '{"diagnosis":"bad","reason":"一直异常","should_retry":true,"retry_hint":"换数据"}'
    graph = build_graph()
    final = graph.invoke({
        "user_input": "植被",
        "region": REGION,
        "user_id": TEST_USER,
        "llm_callbacks": cb,
        "retry_count": 0,
        "max_retries": 2,
    })
    # 达上限后不再重试, 走 output 输出最后一次结果
    assert final.get("retry_count") == 2
    # 最终还是输出了图 (最后一次执行的结果)
    assert final.get("final_output")


def test_pipeline_bad_diagnosis_with_empty_hint_still_counts_retries():
    """diagnosis=bad 但 retry_hint 为空 -> 重试计数仍递增, 不无限循环。

    (回归测试: 递增曾以 retry_hint 非空为前提, 空 hint 会绕过 max_retries,
    循环到 GraphRecursionError。)
    """
    cb = _good_callbacks()
    cb["diagnose"] = lambda p: '{"diagnosis":"bad","reason":"一直异常","should_retry":true,"retry_hint":""}'
    graph = build_graph()
    final = graph.invoke({
        "user_input": "植被",
        "region": REGION,
        "user_id": TEST_USER,
        "llm_callbacks": cb,
        "retry_count": 0,
        "max_retries": 2,
    })
    assert final.get("retry_count") == 2
    assert final.get("final_output")


def test_pipeline_heuristic_bad_diagnosis_still_counts_retries():
    """无 diagnose 回调 (启发式判定异常) -> 重试计数同样递增到上限。

    (回归测试: 启发式分支不设 retry_hint, 曾同样绕过 max_retries。)
    用假平台精确构造: 沙箱试跑指标健康 (生成阶段放行), 全量执行指标异常
    (触发启发式 looks_anomalous -> diagnosis=bad)。
    """
    from unittest.mock import patch

    from src.platform.base import ExecutionResult

    class AnomalousFullPlatform:
        name = "anomalous"

        def execute(self, code, credentials, region, **kwargs):
            if kwargs.get("_sandbox"):
                # 沙箱试跑: 健康指标, 让生成阶段通过
                return ExecutionResult(
                    success=True, metrics={"ndvi_mean": 0.42, "valid_ratio": 0.95}
                )
            # 全量执行: 异常指标 -> 启发式诊断判 bad
            return ExecutionResult(
                success=True,
                output_path="fake_result.jpg",
                metrics={"ndvi_mean": -0.9, "valid_ratio": 0.9},
            )

    cb = _good_callbacks()
    cb.pop("diagnose")  # 走启发式分支

    graph = build_graph()
    with patch("src.agent.nodes.get_platform", lambda n: AnomalousFullPlatform()), \
         patch("src.codegen.sandbox.get_platform", lambda n: AnomalousFullPlatform()):
        final = graph.invoke({
            "user_input": "植被",
            "region": REGION,
            "user_id": TEST_USER,
            "llm_callbacks": cb,
            "retry_count": 0,
            "max_retries": 2,
        })
    assert final.get("retry_count") == 2
    assert final.get("final_output")


def test_pipeline_fails_gracefully_without_credentials(monkeypatch):
    """凭证未存 -> 友好错误 (NO_CREDS), 不裸崩 (P0-5)。"""
    monkeypatch.delenv("REMOTE_SENSING_MASTER_KEY", raising=False)
    cred_mod.delete_credentials(TEST_USER)  # 本测试清掉凭证

    graph = build_graph()
    final = graph.invoke({
        "user_input": "植被",
        "region": REGION,
        "user_id": TEST_USER,
        "llm_callbacks": _good_callbacks(),
        "retry_count": 0,
        "max_retries": 2,
    })
    assert final.get("error")
    assert "凭证" in final["error"]
    assert final.get("error_code") == "NO_CREDS"
    assert "账号" in final.get("error_user_message", "")  # 人话给建议
