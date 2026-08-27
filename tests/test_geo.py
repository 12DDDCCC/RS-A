"""P0-1 地名解析测试: 口语地名 -> 城市 bbox。

守护两条底线:
1. 防幻觉 —— 种子表外的地名必须返回 None, 不许猜坐标;
2. 不瞎猜歧义 —— "朝阳"这类多地同名返回 ambiguous + 候选, 由上层反问。
"""
from __future__ import annotations

import json

import pytest

from src.agent import geo
from src.agent.geo import resolve_place


# ---------- 全名与别名精确匹配 ----------

def test_full_name_match():
    """"北京" 全名命中, high 置信, bbox 四字段齐全且方向不反。"""
    r = resolve_place("北京")
    assert r is not None
    assert r.confidence == "high"
    assert r.name == "北京"
    for k in ("lon_min", "lat_min", "lon_max", "lat_max"):
        assert k in r.bbox
    assert r.bbox["lon_min"] < r.bbox["lon_max"]
    assert r.bbox["lat_min"] < r.bbox["lat_max"]


def test_alias_match_with_suffix():
    """"北京市" 剥离行政后缀后与 "北京" 命中同一条目。"""
    r = resolve_place("北京市")
    assert r is not None
    assert r.confidence == "high"
    assert r.name == "北京"


# ---------- 包含匹配 ----------

def test_contains_match_district():
    """"北京市朝阳区" 截断到市 -> 北京 (种子表刻意不收区级条目)。"""
    r = resolve_place("北京市朝阳区")
    assert r is not None
    assert r.confidence == "high"
    assert r.name == "北京"


def test_contains_match_nearby():
    """"上海周边" -> 上海。"""
    r = resolve_place("上海周边")
    assert r is not None
    assert r.confidence == "high"
    assert r.name == "上海"


# ---------- 未知地名不猜 ----------

def test_unknown_place_returns_none():
    """种子表外 (苏州/纽约/空串) 必须返回 None, 交上层反问。"""
    assert resolve_place("苏州") is None  # 真实城市但不在种子表: 宁可少不可错
    assert resolve_place("纽约") is None
    assert resolve_place("") is None
    assert resolve_place("   ") is None


# ---------- 歧义不拍板 ----------

@pytest.fixture
def ambiguous_chaoyang(tmp_path, monkeypatch):
    """构造含两个 "朝阳" 的临时表, 验证歧义机制。

    种子表本身刻意不收歧义数据 (宁可少不可错), 故用临时表测机制。
    """
    data = {
        "places": [
            {
                "name": "长春",
                "aliases": ["长春市", "朝阳"],  # 长春市朝阳区
                "bbox": {"lon_min": 124.9, "lat_min": 43.6, "lon_max": 125.7, "lat_max": 44.2},
                "bbox_note": "approximate",
            },
            {
                "name": "朝阳",  # 辽宁朝阳市
                "aliases": ["朝阳市"],
                "bbox": {"lon_min": 120.1, "lat_min": 41.3, "lon_max": 120.9, "lat_max": 41.9},
                "bbox_note": "approximate",
            },
        ]
    }
    p = tmp_path / "cn_places.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(geo, "_PLACES_PATH", p)
    geo._load_index.cache_clear()
    yield
    geo._load_index.cache_clear()  # 还原真实表缓存, 防污染其他测试


def test_ambiguous_returns_candidates(ambiguous_chaoyang):
    """"朝阳" 同时命中长春(朝阳区)与朝阳市 -> ambiguous, 列候选不猜。"""
    r = resolve_place("朝阳")
    assert r is not None
    assert r.confidence == "ambiguous"
    assert set(r.ambiguous_cities) == {"长春", "朝阳"}


def test_ambiguous_table_still_resolves_unambiguous_name(ambiguous_chaoyang):
    """歧义表里无歧义的名字 (长春市) 照常 high 命中。"""
    r = resolve_place("长春市")
    assert r is not None
    assert r.confidence == "high"
    assert r.name == "长春"


# ---------- 种子表数据完整性 (防写坏) ----------

def test_seed_table_31_cities_all_resolvable():
    """31 个种子城市全部可解析为 high, bbox 合法且落在中国范围内。"""
    with open(geo._PLACES_PATH, encoding="utf-8") as f:
        places = json.load(f)["places"]
    assert len(places) == 31
    for p in places:
        assert p["bbox_note"] == "approximate", f"{p['name']} 缺近似标注"
        r = resolve_place(p["name"])
        assert r is not None and r.confidence == "high", p["name"]
        assert 73 <= r.bbox["lon_min"] < r.bbox["lon_max"] <= 136
        assert 18 <= r.bbox["lat_min"] < r.bbox["lat_max"] <= 54
