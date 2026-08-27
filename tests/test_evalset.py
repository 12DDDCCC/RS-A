"""评测集测试: 用例数值对齐知识库 + judge_code 对内联好坏码的判定。

好坏码全部是内联字符串 fixture (不走 LLM, 不 mock 生成), 判定完全确定可复现。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.codegen.evaljudge import judge_code
from src.knowledge import load_knowledge

CASES_PATH = Path(__file__).resolve().parents[1] / "evalset" / "cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _case(cid: str) -> dict:
    return next(c for c in CASES if c["id"] == cid)


# ---------- 用例集自身质量 ----------

def test_cases_shape():
    """9 条判定等价类 = 3 任务类型 × 3 地点, 字段齐全无重复 id。"""
    assert len(CASES) == 9
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == 9
    prefixes = {c["id"].split("-")[0] for c in CASES}
    assert prefixes == {"veg", "water", "cd"}
    for c in CASES:
        assert set(c) == {"id", "task", "region", "expect"}
        assert set(c["region"]) == {"lon_min", "lat_min", "lon_max", "lat_max"}
        assert set(c["expect"]) == {"datasets", "bands", "index_direction"}


def test_cases_expect_aligned_with_kb():
    """expect 数值必须与 datasets.json 一致: 数据集在白名单, 波段属于对应数据集。

    防止评测集自己臆造 ID/波段 (禁令第2条同样约束评测集)。
    """
    kb = load_knowledge()
    legal_ids: dict[str, set[str]] = {}
    for ds in kb["datasets"].values():
        cid = ds.get("gee_collection_id")
        if not (cid and ds.get("_verified")):
            continue
        bands = set(ds.get("bands", {})) | {
            b for b in ds.get("mask_bands", {}) if not b.startswith("_")
        }
        legal_ids[cid] = bands
        alt = ds.get("landsat8_collection_id")  # LC08 复用 LC09 波段表
        if alt:
            legal_ids[alt] = bands

    for c in CASES:
        exp = c["expect"]
        unknown = [d for d in exp["datasets"] if d not in legal_ids]
        assert not unknown, f"{c['id']}: 数据集 {unknown} 不在知识库白名单"
        union = set().union(*(legal_ids[d] for d in exp["datasets"]))
        bad = [b for b in exp["bands"] if b not in union]
        assert not bad, f"{c['id']}: 波段 {bad} 不属于期望数据集"


# ---------- 内联 fixture: 好码 (全过) ----------

GOOD_S2_NDVI = (
    "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
    "ndvi = img.normalizedDifference(['B8', 'B4'])\n"
    "result = ndvi"
)
GOOD_S2_MNDWI = (
    "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
    "mndwi = normalizedDifference(['B3', 'B11'])\n"
    "result = mndwi"
)
GOOD_LC08_NDVI = (
    "img = load('LANDSAT/LC08/C02/T1_L2')\n"
    "ndvi = normalizedDifference(['SR_B5', 'SR_B4'])\n"
    "result = ndvi"
)
GOOD_S1_WATER = (
    "img = load('COPERNICUS/S1_GRD')\n"
    "water = img.select('VV').lt(-16)\n"
    "result = water"
)


@pytest.mark.parametrize(
    "code,case_id",
    [
        (GOOD_S2_NDVI, "veg-bj-summer"),
        (GOOD_S2_NDVI, "veg-trl-summer"),
        (GOOD_S2_MNDWI, "water-sh-summer"),   # MNDWI: Green-SWIR 方向合法
        (GOOD_LC08_NDVI, "cd-trl-summer"),    # 时序变化: LC08 也在期望集合
        (GOOD_S1_WATER, "water-trl-summer"),  # SAR 阈值法: 无归一化差也合法
    ],
)
def test_good_codes_pass(code, case_id):
    v = judge_code(code, _case(case_id))
    assert v.passed, f"{case_id} 应通过, 实际原因: {v.reasons}"


# ---------- 内联 fixture: 坏码 (各被拒并给出原因) ----------

def test_reverse_ndvi_call_form_rejected():
    """反向 NDVI (B4 在 B8 前, 调用形态) -> 拒并指出方向。"""
    code = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "ndvi = img.normalizedDifference(['B4', 'B8'])\n"
        "result = ndvi"
    )
    v = judge_code(code, _case("veg-bj-summer"))
    assert not v.passed
    assert any("方向" in r for r in v.reasons)


def test_reverse_ndvi_arith_form_rejected():
    """反向 NDVI 算术形态 (B4 - B8)/(B4 + B8) -> 同样被抓。"""
    code = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "ndvi = (B4 - B8) / (B4 + B8)\n"
        "result = ndvi"
    )
    v = judge_code(code, _case("veg-bj-summer"))
    assert not v.passed
    assert any("方向" in r for r in v.reasons)


def test_hallucinated_dataset_rejected():
    """幻觉数据集 ID -> validator 层确定性拒绝。"""
    code = (
        "img = load('FAKE/SENSING/XYZ_2024')\n"
        "ndvi = normalizedDifference(['B8', 'B4'])\n"
        "result = ndvi"
    )
    v = judge_code(code, _case("veg-bj-summer"))
    assert not v.passed
    assert any("不在白名单" in r for r in v.reasons)


def test_band_mismatch_rejected():
    """错配波段一: 植被用例配 SAR 的 VV -> validator 交叉校验直接拒 (VV 不属于 S2)。"""
    code = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "ndvi = img.select('VV')\n"
        "result = ndvi"
    )
    v = judge_code(code, _case("veg-bj-summer"))
    assert not v.passed
    assert any("不属于代码所用数据集" in r for r in v.reasons)


def test_band_outside_expect_rejected():
    """错配波段二: 水体用例用 B4 (validator 放行, 但不在该任务期望波段表) -> judge 拒。"""
    code = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "ndwi = img.normalizedDifference(['B3', 'B4'])\n"
        "result = ndwi"
    )
    v = judge_code(code, _case("water-sh-summer"))
    assert not v.passed
    assert any("期望波段子集" in r for r in v.reasons)


def test_dataset_not_in_expect_rejected():
    """错配数据集: 植被用例用 S1_GRD -> 不在期望数据集集合。"""
    code = (
        "img = load('COPERNICUS/S1_GRD')\n"
        "water = img.select('VV')\n"
        "result = water"
    )
    v = judge_code(code, _case("veg-bj-summer"))
    assert not v.passed
    assert any("期望集合" in r for r in v.reasons)


def test_scl_cloud_mask_code_not_falsely_rejected():
    """带 SCL 去云掩膜的合法 NDVI 代码不被误杀。

    (回归: expect.bands 是封闭集语义, SCL/QA60 是知识库认可的掩膜波段,
    修复后 mask_bands 全局放行, expect.bands 只圈任务光谱波段。)
    """
    import json as _json
    from pathlib import Path

    from src.codegen.evaljudge import judge_code

    case = _json.loads(
        (Path(__file__).resolve().parents[1] / "evalset" / "cases.json").read_text("utf-8")
    )["cases"][0]
    code = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "scl = img.select('SCL')\n"
        "ndvi = img.normalizedDifference(['B8', 'B4'])\n"
        "result = ndvi"
    )
    v = judge_code(code, case)
    assert v.passed, v.reasons


# ---------- generator 注入失败库反例 (P1-2 闭环) ----------

def test_gen_prompt_injects_failure_examples():
    """失败库有相似历史失败 -> 生成提示词注入 "历史失败反例"; 空库不注入不崩。

    conftest 已全局隔离 _FAILURES_DIR 到 tmp_path, 直接 record_failure 即可。
    """
    from src.codegen import failure_store
    from src.codegen.generator import _build_gen_prompt

    task = "北京夏季植被覆盖监测，计算 NDVI 并输出分布图"
    # 空库: 不注入也不崩 (检索是旁路, 不影响生成)
    plain = _build_gen_prompt(task, {}, "kb", [])
    assert "历史失败反例" not in plain

    failure_store.record_failure(failure_store.make_entry(
        task, "bad code", "validator", "臆造数据集 FAKE/XYZ 不在白名单"
    ))
    prompt = _build_gen_prompt(task, {}, "kb", [])
    assert "历史失败反例" in prompt
    assert "臆造数据集 FAKE/XYZ 不在白名单" in prompt
