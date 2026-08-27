# -*- coding: utf-8 -*-
"""学科验证子 agent 测试 (G4): 六条规则各正反用例。"""
from __future__ import annotations

from src.codegen.domain_validator import verify_domain


# ---------- R1 NDVI 方向 ----------

def test_ndvi_correct_order_passes():
    code = 'ndvi = img.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")'
    assert verify_domain(code).passed


def test_ndvi_reversed_rejected():
    code = 'ndvi = img.normalizedDifference(["B4", "B8"]).rename("NDVI")'
    r = verify_domain(code)
    assert not r.passed and any("方向错误" in i for i in r.issues)


def test_handwritten_red_minus_nir_rejected():
    code = "ndvi = (red_band - nir_band) / (red_band + nir_band)"
    r = verify_domain(code)
    assert not r.passed and any("反向" in i for i in r.issues)


# ---------- R2 S2 SR 缩放 ----------

def test_s2_sr_without_scaling_rejected():
    code = (
        "img = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').median()\n"
        "ndvi = img.normalizedDifference(['B8', 'B4'])\n"
        "nir_mean = img.select('B8').reduceRegion(...).getInfo()"
    )
    r = verify_domain(code)
    assert not r.passed and any("0.0001" in i for i in r.issues)


def test_s2_sr_with_scaling_passes():
    code = (
        "img = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').median().multiply(0.0001)\n"
        "ndvi = img.normalizedDifference(['B8', 'B4'])"
    )
    assert verify_domain(code).passed


# ---------- R3 Landsat 缩放 ----------

def test_landsat_sr_without_scaling_rejected():
    code = (
        "img = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2').median()\n"
        "ndvi = img.normalizedDifference(['SR_B5', 'SR_B4'])"
    )
    r = verify_domain(code)
    assert not r.passed and any("0.0000275" in i for i in r.issues)


def test_landsat_with_scaling_passes():
    code = (
        "img = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2').median()\n"
        "sr = img.select(['SR_B5', 'SR_B4']).multiply(0.0000275).add(-0.2)\n"
        "ndvi = sr.normalizedDifference(['SR_B5', 'SR_B4'])"
    )
    # 注意: 该代码同时触发 R3 检查, 但含 0.0000275 -> 通过
    assert verify_domain(code).passed


# ---------- R4 NDWI / MNDWI 波段对 ----------

def test_mndwi_with_nir_rejected():
    code = (
        "mndwi = img.normalizedDifference(['B3', 'B8'])\n"  # Green+NIR 是 NDWI
    )
    r = verify_domain(code)
    assert not r.passed and any("MNDWI" in i for i in r.issues)


def test_ndwi_with_swir_rejected():
    code = (
        "ndwi = img.normalizedDifference(['B3', 'B11'])\n"  # Green+SWIR 是 MNDWI
    )
    r = verify_domain(code)
    assert not r.passed and any("MNDWI" in i or "NDWI" in i for i in r.issues)


# ---------- R5 NDBI 方向 ----------

def test_ndbi_reversed_rejected():
    code = "ndbi = img.normalizedDifference(['SR_B5', 'SR_B6']).rename('NDBI')"  # NIR-SWIR 反了
    r = verify_domain(code)
    assert not r.passed and any("NDBI 方向错误" in i for i in r.issues)


def test_ndbi_correct_passes():
    code = "ndbi = img.normalizedDifference(['SR_B6', 'SR_B5']).rename('NDBI')"
    assert verify_domain(code).passed


# ---------- R6 S1 云掩膜 ----------

def test_s1_cloud_filter_rejected():
    code = (
        "s1 = ee.ImageCollection('COPERNICUS/S1_GRD')\\\n"
        "    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))"
    )
    r = verify_domain(code)
    assert not r.passed and any("SAR" in i for i in r.issues)


def test_s1_without_cloud_passes():
    code = "s1 = ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(geom)"
    assert verify_domain(code).passed
