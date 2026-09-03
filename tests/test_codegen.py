"""三层防护测试: 验证每层都能拦住对应类型的恶意代码。

验证标准: 故意写反波段的代码被 validator 拒绝 (禁令第5条的硬护栏)。
凭证流 (P0-5): generator/sandbox 只收 user_id, 凭证由 fixture 预存。
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from src.codegen.generator import generate_and_validate
from src.codegen.reviewer import review_code
from src.codegen.validator import validate_code
from src.io import credentials as cred_mod

REGION = {"lon_min": 116.0, "lat_min": 39.0, "lon_max": 117.0, "lat_max": 40.0}
TEST_USER = "codegen_user"


@pytest.fixture(autouse=True)
def _seed_creds(monkeypatch):
    """沙箱执行瞬间要解密凭证: 预存一份测试凭证。"""
    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    cred_mod.store_credentials(TEST_USER, {"pie_token": "test"})
    yield


# ---------- 第一层 validator ----------

def test_validator_rejects_hallucinated_dataset_id():
    """幻觉的数据集 ID 必须被白名单拒绝。"""
    code = "ds = load('FAKE/NOT_REAL/DATASET')\nresult = ds"
    r = validate_code(code)
    assert not r.passed
    assert any("不在白名单" in i for i in r.issues)


def test_validator_accepts_real_dataset_id():
    """核实的真实 ID 放行。"""
    code = "ds = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = ds"
    r = validate_code(code)
    assert r.passed


def test_validator_rejects_syntax_error():
    """语法错误立即拦下, 不再继续。"""
    code = "def foo(:\n    pass"
    r = validate_code(code)
    assert not r.passed
    assert any("语法错误" in i for i in r.issues)


def test_validator_rejects_cross_dataset_band():
    """Landsat 数据集配 S2 的 B8 波段 -> 交叉校验拒绝 (数据集/波段错配)。"""
    code = (
        "img = load('LANDSAT/LC09/C02/T1_L2')\n"
        "ndvi = normalizedDifference(['B8', 'B4'])"
    )
    r = validate_code(code)
    assert not r.passed
    assert any("不属于代码所用数据集" in i for i in r.issues)


def test_validator_accepts_matching_dataset_band():
    """数据集与波段匹配 (Landsat + SR_B5/SR_B4) -> 放行。"""
    code = (
        "img = load('LANDSAT/LC09/C02/T1_L2')\n"
        "ndvi = normalizedDifference(['SR_B5', 'SR_B4'])"
    )
    r = validate_code(code)
    assert r.passed


def test_validator_accepts_landsat8_id():
    """LC08 ID 在白名单内 (知识库注明其波段编号与 LC09 相同) -> 放行。"""
    code = (
        "img = load('LANDSAT/LC08/C02/T1_L2')\n"
        "ndvi = normalizedDifference(['SR_B5', 'SR_B4'])"
    )
    r = validate_code(code)
    assert r.passed


def test_validator_catches_b8a_hallucination():
    """B8A 是 S2 专属窄近红外波段: 配 Landsat -> 错配拒绝; 配 S2_SR -> 放行。"""
    bad = (
        "img = load('LANDSAT/LC09/C02/T1_L2')\n"
        "ndvi = normalizedDifference(['B8A', 'B4'])"
    )
    r_bad = validate_code(bad)
    assert not r_bad.passed
    assert any("不属于代码所用数据集" in i for i in r_bad.issues)

    good = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "ndvi = normalizedDifference(['B8A', 'B4'])"
    )
    r_good = validate_code(good)
    assert r_good.passed


def test_validator_captures_s2_mask_bands():
    """SCL/QA60 掩膜波段能被正则捕获: 配 S2_SR 放行, 配 Landsat 错配拒绝。"""
    good = (
        "img = load('COPERNICUS/S2_SR_HARMONIZED')\n"
        "scl = img.select('SCL')\n"
        "cloud = img.select('QA60')"
    )
    r_good = validate_code(good)
    assert r_good.passed
    assert {"SCL", "QA60"} <= set(r_good.used_bands)

    bad = "img = load('LANDSAT/LC08/C02/T1_L2')\nscl = img.select('SCL')"
    r_bad = validate_code(bad)
    assert not r_bad.passed
    assert any("不属于代码所用数据集" in i for i in r_bad.issues)


# ---------- 第二层 reviewer ----------

def test_reviewer_catches_reverse_ndvi_via_rules():
    """规则兜底审查能抓 NDVI 反向 (B4 在 B8 前)。"""
    code = "ndvi = (B4 - B8) / (B4 + B8)"
    r = review_code(code, "植被", "", llm_review=None)
    assert not r.approved
    assert any("反向" in c for c in r.comments)


def test_reviewer_catches_cloud_field_misuse():
    """Landsat 用了 S2 的云量字段 -> 拒绝。"""
    code = "landsat.filter('CLOUDY_PIXEL_PERCENTAGE')"
    r = review_code(code, "时序", "", llm_review=None)
    assert not r.approved


def test_reviewer_llm_callback_approved():
    """LLM 回调返回 APPROVED -> 通过。"""
    code = "x = 1"
    r = review_code(code, "t", "", llm_review=lambda p: "APPROVED\n代码合理")
    assert r.approved


def test_reviewer_llm_callback_rejected():
    r = review_code("x=1", "t", "", llm_review=lambda p: "REJECTED\n波段反了")
    assert not r.approved


# ---------- 第三层 sandbox ----------

def test_sandbox_timeout_rejects_execution():
    """试跑超时 -> 拒绝全量执行 (timeout_s 真实生效, 不是死配置)。"""
    import time

    from src.codegen.sandbox import SandboxConfig, sandbox_trial
    from src.platform.base import ExecutionResult

    class SlowPlatform:
        name = "slow"

        def execute(self, code, credentials, region, **kwargs):
            time.sleep(2.0)
            return ExecutionResult(success=True)

    r = sandbox_trial(
        "x = 1", TEST_USER, REGION,
        platform=SlowPlatform(),
        config=SandboxConfig(timeout_s=1),
    )
    assert not r.success
    assert "超时" in r.error


def test_sandbox_rejects_anomalous_result():
    """沙箱结果异常 (NDVI 全负) -> ready=False。"""
    # 用 mock LLM 生成一段会通过 validator 的代码, 但强制 mock 出异常指标
    # 这里直接测 generator 在沙箱异常时的行为
    def fake_gen(prompt):
        return "ndvi = (B8 - B4) / (B8 + B4)\nresult = load('COPERNICUS/S2_SR_HARMONIZED')"

    # 通过注入 monkey 方式让 sandbox 返回异常: 用一个返回异常指标的假平台
    from src.codegen import sandbox as sb_mod

    original_get_platform = sb_mod.get_platform

    class FakePlatform:
        name = "fake"

        def execute(self, code, credentials, region, **kwargs):
            from src.platform.base import ExecutionResult

            return ExecutionResult(
                success=True,
                metrics={"ndvi_mean": -0.9, "valid_ratio": 0.9},  # 异常
            )

    sb_mod.get_platform = lambda name: FakePlatform()
    try:
        result = generate_and_validate("植被变化", REGION, TEST_USER, fake_gen)
        assert not result.ready
        assert any("沙箱" in f for f in result.feedback_history)
    finally:
        sb_mod.get_platform = original_get_platform


def test_full_pipeline_passes_for_good_code():
    """好代码: 三层全过 -> ready=True。"""
    def fake_gen(prompt):
        return "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img"

    result = generate_and_validate("植被变化", REGION, TEST_USER, fake_gen)
    assert result.ready
    assert result.attempts >= 1


def test_pipeline_retries_on_validator_failure():
    """第一次幻觉 -> 带反馈重试 -> 第二次正确 -> 通过。"""
    calls = {"n": 0}

    def fake_gen(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            # 幻觉数据集
            return "img = load('FAKE/ID/HERE')\nresult = img"
        return "img = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img"

    result = generate_and_validate("植被", REGION, TEST_USER, fake_gen)
    assert result.ready
    assert calls["n"] == 2
    assert len(result.feedback_history) == 1


def test_pipeline_hits_max_attempts():
    """始终幻觉 -> 达上限 -> ready=False, 不无限循环。"""
    def fake_gen(prompt):
        return "img = load('FAKE/ID/HERE')\nresult = img"

    result = generate_and_validate("植被", REGION, TEST_USER, fake_gen, max_attempts=2)
    assert not result.ready
    assert result.attempts == 2


def test_pipeline_breaks_on_gee_network_error(monkeypatch):
    """沙箱网络故障 (GEE_NETWORK 哨兵) 立即失败, 不再烧 LLM 重试 (2026-09-01)。

    修复前: 网络不通被当"代码超时", 3 轮重试每轮 60s 沙箱超时 + LLM 生成。
    """
    from src.codegen import generator as gen_mod
    from src.platform.base import ExecutionResult

    calls = {"n": 0}

    def fake_gen(prompt):
        calls["n"] += 1
        return "img = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img"

    def fake_sandbox(code, user_id, region, **kw):
        return ExecutionResult(success=False,
                               error="GEE_NETWORK: 连接 Google 服务超时 (>30s)")

    monkeypatch.setattr(gen_mod, "sandbox_trial", fake_sandbox)
    result = generate_and_validate("植被", REGION, TEST_USER, fake_gen, max_attempts=3)
    assert not result.ready
    assert calls["n"] == 1          # 第一轮网络失败后立即断路, 没有第二轮
    assert "GEE_NETWORK" in result.feedback_history[-1]


def test_validator_alt_cid_includes_mask_bands():
    """备用 Collection ID (LC08) 的波段表必须含掩膜波段 (2026-09-01 顺序修复)。

    修复前: mask_bands 在备用 ID 拷贝之后才并入主表 -> LC08 永远缺 QA_PIXEL,
    计划选 LC08+QA_PIXEL 被 planning/validator 冤杀。
    """
    from src.codegen.validator import DATASET_BANDS

    lc08 = DATASET_BANDS.get("LANDSAT/LC08/C02/T1_L2")
    assert lc08 is not None
    assert "QA_PIXEL" in lc08 and "QA_RADSAT" in lc08
    assert "SR_B5" in lc08  # 主表波段仍在
