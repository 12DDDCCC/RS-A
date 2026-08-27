# -*- coding: utf-8 -*-
"""B2-fast 桥接层测试: /domain/verify + /prompts/domain 端点 与 MCP 五工具。

dsh (dsh-mcp-client) 与 Claude Desktop 都经 mcp_server.py 调后端;
本文件守护桥接契约: 工具清单完整性 / 阻塞语义 (execute_and_wait) /
澄清续跑协议 / 学科校验透传。
MCP 侧不出网 —— monkeypatch mcp._api, 只验证调用序列与文本组装。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.mcp_server as mcp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("REMOTE_SENSING_MASTER_KEY", raising=False)
    from src.main import app

    return TestClient(app)


# ---------- HTTP 端点 ----------

GOOD_CODE = """
var col = ee.ImageCollection('COPERNICUS/S2_SR').filterDate('2024-06-01','2024-06-30');
var scaled = col.map(function(img){ return img.multiply(0.0001); });
var ndvi = scaled.normalizedDifference(['B8','B4']);
"""

BAD_CODE = """
var ndvi = img.normalizedDifference(['B4','B8']);
"""


def test_domain_verify_pass(client):
    r = client.post("/domain/verify", json={"code": GOOD_CODE})
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["issues"] == []


def test_domain_verify_catches_ndvi_reversed(client):
    r = client.post("/domain/verify", json={"code": BAD_CODE})
    body = r.json()
    assert body["passed"] is False
    assert any("NDVI" in i for i in body["issues"])


def test_domain_prompts_three_sections(client):
    r = client.get("/prompts/domain")
    assert r.status_code == 200
    body = r.json()
    assert body["prompt_version"]  # 版本号必在 (升级归因依赖)
    secs = body["sections"]
    assert set(secs) == {"expert_persona", "four_resolutions", "landcover_six_classes"}
    assert "遥感图像处理专家" in secs["expert_persona"]
    assert "四分辨率" in secs["four_resolutions"]
    assert "六类" in secs["landcover_six_classes"]


def test_knowledge_catalog_summary(client):
    r = client.get("/knowledge/catalog")
    assert r.status_code == 200
    body = r.json()
    keys = {d["key"] for d in body["datasets"]}
    assert "sentinel2_sr" in keys and "sentinel1_grd" in keys
    s2 = next(d for d in body["datasets"] if d["key"] == "sentinel2_sr")
    assert s2["gee_collection_id"].startswith("COPERNICUS/")
    assert "B4" in s2["bands"] and "B8" in s2["bands"]
    assert {"NDVI", "MNDWI", "NDBI"} <= set(body["indices"])


# ---------- MCP 工具 ----------

class _Recorder:
    """按 (method, path 前缀) 返回预设响应; 记录调用序列供断言。"""

    def __init__(self, routes):
        self.routes = routes  # [(method, path_prefix, value|callable)]
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, path, body=None, raw=False):
        self.calls.append((method, path))
        for m, pfx, val in self.routes:
            if method == m and path.startswith(pfx):
                return val(self.calls) if callable(val) else val
        raise AssertionError(f"未预设的调用: {method} {path}")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(mcp.time, "sleep", lambda s: None)


def test_tools_list_has_six():
    names = {t["name"] for t in mcp.TOOLS}
    assert names == {"analyze_area", "execute_and_wait", "verify_domain_code",
                     "get_task_status", "get_result_image", "get_dataset_catalog"}


def test_get_dataset_catalog_tool(monkeypatch):
    rec = _Recorder([("GET", "/knowledge/catalog", {
        "datasets": [{"key": "sentinel2_sr", "gee_collection_id": "COPERNICUS/S2_SR_HARMONIZED",
                      "purpose": "植被分析默认", "resolution_m": 10,
                      "temporal_coverage": "2017-03-28 至今",
                      "bands": {"B4": "红(Red)", "B8": "近红外(NIR)"}}],
        "indices": {"NDVI": {}, "MNDWI": {}},
    })])
    monkeypatch.setattr(mcp, "_api", rec)
    out = mcp.call_tool("get_dataset_catalog", {})
    assert "sentinel2_sr" in out and "NDVI" in out


def test_verify_domain_code_tool_passes_through(monkeypatch):
    rec = _Recorder([("POST", "/domain/verify", {"passed": False,
                                                  "issues": ["NDVI 方向错误"]})])
    monkeypatch.setattr(mcp, "_api", rec)
    out = mcp.call_tool("verify_domain_code", {"code": "var x=1;"})
    assert ("POST", "/domain/verify") in rec.calls
    assert "NDVI 方向错误" in out


def test_execute_and_wait_done_path(monkeypatch):
    poll = iter([{"status": "running", "phase_text": "正在分析中"},
                 {"status": "done",
                  "caption": "分析区域：北京 平均植被指数约 0.47"}])
    rec = _Recorder([
        ("POST", "/analyze", {"task_id": "T1"}),
        ("GET", "/tasks/T1", lambda calls: next(poll)),
    ])
    monkeypatch.setattr(mcp, "_api", rec)
    out = mcp.call_tool("execute_and_wait", {"user_input": "分析北京植被"})
    assert "T1" in out and "0.47" in out and "get_result_image" in out


def test_execute_and_wait_clarify_then_resume(monkeypatch):
    # 第一轮: 新任务 -> need_clarify; 第二轮带 task_id+clarifications 续跑至 done
    polls = [{"status": "need_clarify", "question": "要哪个季节?"},
             {"status": "running"},
             {"status": "done", "caption": "图说X"}]

    def next_poll(calls):
        return polls.pop(0) if len(polls) > 1 else polls[0]

    rec = _Recorder([
        ("POST", "/analyze", {"task_id": "T2"}),
        ("POST", "/tasks/T2/answer", {"task_id": "T2", "status": "queued"}),
        ("GET", "/tasks/T2", next_poll),
    ])
    monkeypatch.setattr(mcp, "_api", rec)
    out1 = mcp.call_tool("execute_and_wait", {"user_input": "南京植被"})
    assert "要哪个季节?" in out1 and "T2" in out1
    out2 = mcp.call_tool("execute_and_wait",
                         {"task_id": "T2", "clarifications": "夏季",
                          "user_input": "南京植被"})
    assert "完成" in out2 and "图说X" in out2
    assert ("POST", "/tasks/T2/answer") in rec.calls  # 走了续跑而非新任务


def test_execute_and_wait_resume_requires_answer(monkeypatch):
    called = []

    def guard(*a, **kw):
        called.append(a)

    monkeypatch.setattr(mcp, "_api", guard)
    out = mcp.call_tool("execute_and_wait", {"user_input": "x", "task_id": "T9"})
    assert "clarifications" in out and not called  # 守卫先行, 不打 API


def test_execute_and_wait_failed_path(monkeypatch):
    rec = _Recorder([
        ("POST", "/analyze", {"task_id": "T3"}),
        ("GET", "/tasks/T3", {"status": "failed",
                              "error": {"message": "没找到这个地方",
                                        "suggestion": "换一个城市名"}}),
    ])
    monkeypatch.setattr(mcp, "_api", rec)
    out = mcp.call_tool("execute_and_wait", {"user_input": "分析亚特兰蒂斯"})
    assert "失败" in out and "换一个城市名" in out


def test_execute_and_wait_timeout_returns_task_id(monkeypatch):
    rec = _Recorder([
        ("POST", "/analyze", {"task_id": "T4"}),
        ("GET", "/tasks/T4", {"status": "running"}),
    ])
    monkeypatch.setattr(mcp, "_api", rec)
    out = mcp.call_tool("execute_and_wait",
                        {"user_input": "北京", "timeout_s": 0})
    assert "超时" in out and "T4" in out and "get_task_status" in out
