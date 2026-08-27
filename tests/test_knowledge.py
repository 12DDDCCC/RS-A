"""数据集知识库的防幻觉测试。

这些测试守护事实地基:一旦 datasets.json 里的波段顺序、云量字段、缩放系数
被写错(或被误改),测试会立即失败。对应 arXiv 论文统计的头号死因
(API/波段幻觉),是三层防护的第一层。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.knowledge import get_dataset, index_formula, list_datasets, load_knowledge

KNOWLEDGE = load_knowledge()
DATASETS = KNOWLEDGE["datasets"]


# ---------- 结构完整性 ----------

def test_knowledge_loads_and_has_datasets():
    """知识库能加载,且至少包含 S2/Landsat/S1 三大数据源。"""
    assert "datasets" in KNOWLEDGE
    names = list_datasets()
    for required in ("sentinel2_sr", "landsat9_c2_l2", "sentinel1_grd"):
        assert required in names, f"缺少核心数据集: {required}"


def test_every_dataset_has_collection_id_and_source():
    """每个数据集必须有 collection_id 和来源 URL (溯源,防编造)。"""
    for name, ds in DATASETS.items():
        assert ds.get("gee_collection_id"), f"{name} 缺 gee_collection_id"
        assert ds.get("source_url"), f"{name} 缺 source_url"
        assert ds.get("_verified") in (True, False), f"{name} 缺 _verified 标注"


def test_unverified_datasets_are_marked():
    """未核实的数据集必须显式标 _verified=false,不能假装已核实。"""
    for name, ds in DATASETS.items():
        if ds.get("_verified") is False:
            # 未核实的必须有说明为什么
            assert ds.get("_note") or ds.get("action_required"), (
                f"{name} 标记未核实但没说明原因"
            )


# ---------- NDVI 方向不能反 (头号幻觉陷阱) ----------

def test_sentinel2_ndvi_correct_order():
    """Sentinel-2 NDVI 必须是 (NIR - Red)/(NIR + Red) = (B8-B4)/(B8+B4)。

    反过来会让植被全为负值,且图看起来"像对的" —— 这是遥感最致命的失败。
    """
    formula = index_formula("sentinel2_sr", "NDVI")
    formula_nospace = formula.replace(" ", "")
    # 分子必须 NIR - Red: B8-B4
    assert "B8-B4" in formula_nospace, f"NDVI 分子方向错误: {formula}"
    assert "(B8+B4)" in formula_nospace, f"NDVI 分母错误: {formula}"
    # 严禁反向
    assert "B4-B8" not in formula_nospace, f"NDVI 反向! {formula}"


def test_landsat_ndvi_correct_order():
    """Landsat NDVI = (SR_B5 - SR_B4)/(SR_B5 + SR_B4), NIR 在前。"""
    formula = index_formula("landsat9_c2_l2", "NDVI")
    fn = formula.replace(" ", "")
    assert "SR_B5-SR_B4" in fn, f"Landsat NDVI 分子错误: {formula}"
    assert "SR_B4-SR_B5" not in fn, f"Landsat NDVI 反向! {formula}"


# ---------- 云量字段不可跨数据集互换 ----------

def test_cloud_fields_are_distinct_and_correct():
    """S2 用 CLOUDY_PIXEL_PERCENTAGE,Landsat 用 CLOUD_COVER,不可互换。"""
    assert DATASETS["sentinel2_sr"]["cloud_field"] == "CLOUDY_PIXEL_PERCENTAGE"
    assert DATASETS["landsat9_c2_l2"]["cloud_field"] == "CLOUD_COVER"
    # 交叉验证:确认两者不同
    assert (
        DATASETS["sentinel2_sr"]["cloud_field"]
        != DATASETS["landsat9_c2_l2"]["cloud_field"]
    )


def test_cloud_field_warning_present():
    """知识库必须有云量字段不可互换的警示。"""
    warn = KNOWLEDGE.get("cloud_field_warning", {})
    assert warn.get("sentinel2") == "CLOUDY_PIXEL_PERCENTAGE"
    assert warn.get("landsat") == "CLOUD_COVER"


# ---------- 缩放系数正确 ----------

def test_sentinel2_scale_factor():
    """S2 SR 缩放: DN × 0.0001。"""
    sf = DATASETS["sentinel2_sr"]["scale_factor"]
    assert sf["operation"] == "multiply"
    assert sf["value"] == pytest.approx(0.0001)


def test_landsat_scale_factor():
    """Landsat C2 L2 缩放: DN × 0.0000275 − 0.2 (USGS 官方)。"""
    sf = DATASETS["landsat9_c2_l2"]["scale_factor"]
    assert sf["operation"] == "affine"
    assert sf["scale"] == pytest.approx(0.0000275)
    assert sf["offset"] == pytest.approx(-0.2)


# ---------- NDWI / MNDWI 同名陷阱 ----------

def test_ndwi_vs_mndwi_are_different_formulas():
    """NDWI(McFeeters) 用 NIR,MNDWI(Xu) 用 SWIR,绝不可混用。"""
    s2 = DATASETS["sentinel2_sr"]["key_indices"]
    # NDWI 含 NIR(B8),不含 SWIR(B11)
    assert "B8" in s2["NDWI"], "NDWI 应含 NIR"
    assert "B11" not in s2["NDWI"], "NDWI 不应含 SWIR"
    # MNDWI 含 SWIR(B11)
    assert "B11" in s2["MNDWI"], "MNDWI 应含 SWIR"
    assert "MNDWI" != "NDWI"


# ---------- 波段波长合理性 ----------

def test_sentinel2_band_wavelengths_ordered():
    """S2 波段波长应随波段号递增 (物理常识,防波段张冠李戴)。"""
    bands = DATASETS["sentinel2_sr"]["bands"]
    waves = [(b, bands[b]["wavelength_nm"]) for b in bands]
    for i in range(len(waves) - 1):
        assert waves[i][1] < waves[i + 1][1], (
            f"波段 {waves[i][0]} 波长 {waves[i][1]} >= {waves[i+1][0]} {waves[i+1][1]}"
        )
    # 红光 B4 (~664nm) < 近红外 B8 (~835nm),关键关系
    assert bands["B4"]["wavelength_nm"] < bands["B8"]["wavelength_nm"]


# ---------- JSON 可被解析 ----------

def test_datasets_json_is_valid_and_file_matches():
    """磁盘上的 datasets.json 必须是合法 JSON,且与加载结果一致。"""
    path = Path(__file__).parent.parent / "src" / "knowledge" / "datasets.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw == KNOWLEDGE
