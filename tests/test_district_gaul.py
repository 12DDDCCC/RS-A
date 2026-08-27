# -*- coding: utf-8 -*-
"""区县级默认精度 (GAUL) 测试: guess_district 识别 + /analyze 兜底分支 +
生成提示 GAUL 块 + node_generate 守卫放行。

设计: 用户未给经纬度且城市表未命中时, "XX区/XX县/XX旗" 名字走
region=None + region_source=gaul_level2, 边界由生成代码从
FAO/GAUL/2015/level2 动态解析; 都识别不到仍 400 (防幻觉铁律不放松)。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from src.agent.geo import guess_district


# ---------- guess_district 纯函数 ----------

@pytest.mark.parametrize("text,expect", [
    ("分析江宁区的植被", "江宁区"),
    ("玄武区 2024 年水体变化", "玄武区"),
    ("看看曲水县", "曲水县"),
    ("鄂托克前旗草场", "鄂托克前旗"),
    ("浦东新区土地利用", "浦东新区"),
])
def test_guess_district_hits(text, expect):
    assert guess_district(text) == expect


@pytest.mark.parametrize("text", [
    "分析植被状况",          # 无地名
    "北京植被",              # 城市名 (含"京"? 无后缀) — 不识别
    "这个区很好看",          # 前缀过短
    " 区域范围大一些",        # "区域"的"区"前缀是"地"单字 — 不识别
])
def test_guess_district_misses(text):
    assert guess_district(text) is None


def test_guess_district_city_priority():
    # 城市表内的名字 (如 "朝阳" 在歧义表) 不被区县识别抢先 —— 该函数
    # 本就在城市解析失败后调用, 这里防子串误报
    assert guess_district("北京市朝阳区") is None  # "朝阳区"前缀"朝阳"在城市/歧义域


# ---------- /analyze 区县兜底 ----------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    from src.io import auth as auth_mod
    from src.io import credentials as cred_mod

    for uid in ("gu1",):
        cred_mod.delete_credentials(uid)
        auth_mod.delete_access_token(uid)
    from src.main import app

    c = TestClient(app)
    r = c.post("/credentials", json={"user_id": "gu1",
                                     "credentials": {"pie_token": "t"}})
    assert r.status_code == 200
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def test_analyze_district_accepted(client):
    """区县名不再 400: region=None + place=区县 + region_source=gaul_level2。"""
    r = client.post("/analyze", json={"user_id": "gu1",
                                      "user_input": "分析江宁区的植被状况"})
    assert r.status_code == 202
    assert r.json()["status"] == "queued"


def test_analyze_explicit_district_place(client):
    """place 字段直接给区县名 (编排层可能传) -> GAUL 模式而非 400。"""
    r = client.post("/analyze", json={"user_id": "gu1",
                                      "user_input": "植被状况",
                                      "place": "江宁区"})
    assert r.status_code == 202


def test_analyze_city_plus_district_upgrades(client):
    """城市+区县并存 (place=南京/原文含江宁区) -> 提级为区县 GAUL 精度。"""
    r = client.post("/analyze", json={"user_id": "gu1",
                                      "user_input": "南京市江宁区的植被",
                                      "place": "南京"})
    assert r.status_code == 202


def test_analyze_city_without_district_stays_bbox(client, monkeypatch):
    """纯城市名保持市级 bbox (无区县时不强行 GAUL)。"""
    captured = {}

    class _Store:
        def create(self, user_id, state):
            captured.update(state)
            return "t-bbox"

        def has_running(self, uid):
            return False

    import src.main as main_mod

    monkeypatch.setattr(main_mod, "store", _Store())
    monkeypatch.setattr(main_mod, "run_job", lambda *a, **k: None)
    r = client.post("/analyze", json={"user_id": "gu1",
                                      "user_input": "北京的植被",
                                      "place": "北京"})
    assert r.status_code == 202
    assert captured["region"] is not None
    assert captured["region_source"] == "bbox"


def test_analyze_no_place_still_400(client):
    """完全无地名仍 400 —— '自行判断'止于确定性形态识别, 不瞎猜坐标。"""
    r = client.post("/analyze", json={"user_id": "gu1",
                                      "user_input": "分析植被状况"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_REGION"


# ---------- 生成提示与节点守卫 ----------

def test_build_gen_prompt_district_uses_bbox():
    from src.codegen.generator import _build_gen_prompt

    # 区县边界已由 node_generate 前置解析为 bbox, 生成提示与城市模式同构
    p = _build_gen_prompt("江宁区植被", {"lon_min": 118.5}, "知识", [], "江宁区")
    assert "# 区域\n{'lon_min': 118.5}" in p
    p2 = _build_gen_prompt("北京植被", {"lon_min": 1}, "知识", [])
    assert "# 区域\n{'lon_min': 1}" in p2


def test_district_pinyin():
    from src.agent.geo import district_pinyin

    assert district_pinyin("江宁区") == "Jiangning"
    assert district_pinyin("曲水县") == "Qushui"
    assert district_pinyin("浦东新区") == "Pudongxin"


def test_node_generate_resolves_district(monkeypatch):
    from src.agent import nodes

    called = {}

    def fake_resolve(place, user_id):
        called["args"] = (place, user_id)
        return {"lon_min": 118.5, "lat_min": 31.6, "lon_max": 119.1, "lat_max": 32.1}

    monkeypatch.setattr("src.agent.geo.resolve_district_bbox", fake_resolve)
    state = {"region": None, "region_source": "district", "place": "江宁区",
             "user_id": "u1", "llm_callbacks": {}}
    nodes.node_generate(state)
    assert called["args"] == ("江宁区", "u1")
    assert state["region"]["lon_min"] == 118.5


def test_node_generate_district_unresolved(monkeypatch):
    from src.agent import nodes

    monkeypatch.setattr("src.agent.geo.resolve_district_bbox", lambda p, u: None)
    state = {"region": None, "region_source": "district", "place": "无名区",
             "user_id": "u1", "llm_callbacks": {}}
    out = nodes.node_generate(state)
    assert out.get("error_code") == "DISTRICT_UNRESOLVED"


def test_node_generate_guard_rejects_bare_missing_region():
    from src.agent import nodes

    # 无 region 且非 district 模式 -> 缺区域报错 (防幻觉铁律)
    state = {"region": None, "place": "某地", "llm_callbacks": {}}
    out = nodes.node_generate(state)
    assert "缺少区域信息" in (out.get("error") or "")


def test_prompt_version_bumped():
    from src.agent.prompts import PROMPT_VERSION

    # 2.3: 分类/等级成果图强制图例; 升级 prompt 必须递增版本号 (E-续纪律),
    # 断言下界而非锁死 —— 后续升版无需回改本测试
    assert PROMPT_VERSION >= "2.2"
