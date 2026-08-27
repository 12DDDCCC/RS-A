"""第四层防护测试: 锚点评测 (结果物理合理性, P1-1)。

验证标准 (设计蓝图):
  - P1 值域: 归一化指数超 [-1,1] -> hard_fail
  - P3 符号: 反向 NDVI (-0.4, 旧 -0.5 阈值漏掉) -> hard_fail
  - R1 区域先验: 超先验区间 -> 仅 suspicious
  - base.py 委托改造 + Mock PIE 扩展指标自洽
"""
from __future__ import annotations

from src.codegen.anchors import AnchorReport, check_anchors
from src.platform.base import ExecutionResult
from src.platform.pie_adapter import PIEEngineAdapter

# 北京中心城区近似框 (中心点落入 anchors.json 北京锚点 bbox)
BEIJING_BBOX = {"lon_min": 116.0, "lat_min": 39.9, "lon_max": 116.8, "lat_max": 40.1}


def test_normal_metrics_ok():
    """正常指标 (值域内 + 符号自洽) -> ok, 零命中。"""
    r = check_anchors("", {
        "ndvi_mean": 0.42, "nir_mean": 0.35, "red_mean": 0.22, "valid_ratio": 0.95,
    })
    assert isinstance(r, AnchorReport)
    assert r.verdict == "ok"
    assert r.hits == []


def test_p1_index_range_violation_hard_fail():
    """ndvi_mean=1.5 超出 [-1,1] 值域 -> hard_fail。"""
    r = check_anchors("", {"ndvi_mean": 1.5, "valid_ratio": 0.95})
    assert r.verdict == "hard_fail"
    assert any(h["rule_id"].startswith("P1_index_range") for h in r.hits)


def test_p1_also_covers_other_nd_indices():
    """其他归一化指数 (ndwi_mean=-1.2) 同受值域硬界约束。"""
    r = check_anchors("", {"ndwi_mean": -1.2})
    assert r.verdict == "hard_fail"


def test_p3_reversed_ndvi_hard_fail():
    """反向 NDVI: ndvi=-0.4 但 nir>red, 符号矛盾 -> hard_fail (旧 -0.5 阈值抓不住)。"""
    r = check_anchors("", {"ndvi_mean": -0.4, "nir_mean": 0.35, "red_mean": 0.22})
    assert r.verdict == "hard_fail"
    assert any(h["rule_id"] == "P3_ndvi_sign" for h in r.hits)


def test_p3_skips_near_zero_diff():
    """nir≈red (差 < 0.01) -> 跳过 P3, 不制造除零噪声冤案。"""
    r = check_anchors("", {"ndvi_mean": -0.4, "nir_mean": 0.22, "red_mean": 0.225})
    assert r.verdict == "ok"


def test_r1_region_prior_suspicious_not_hard():
    """北京夏季 ndvi=0.9 超先验上限 -> suspicious (先验仅提醒, 非 hard_fail)。"""
    r = check_anchors("", {
        "ndvi_mean": 0.9, "nir_mean": 0.5, "red_mean": 0.1,
        "region_bbox": BEIJING_BBOX, "season": "summer",
    })
    assert r.verdict == "suspicious"
    assert any(h["rule_id"].startswith("R1_region_prior") for h in r.hits)


def test_r1_requires_bbox_and_season():
    """缺 region_bbox/season -> R1 不启用 (0.9 不报, 防误伤)。"""
    assert check_anchors("", {"ndvi_mean": 0.9}).verdict == "ok"
    assert check_anchors("", {"ndvi_mean": 0.9, "season": "summer"}).verdict == "ok"


def test_empty_metrics_ok():
    """空 metrics -> ok (向后兼容)。"""
    assert check_anchors("", {}).verdict == "ok"


def test_base_delegates_to_anchors():
    """base.py 委托: -0.4 反向 NDVI (旧兜底抓不住) 经锚点 P3 判异常。"""
    bad = ExecutionResult(
        success=True, metrics={"ndvi_mean": -0.4, "nir_mean": 0.35, "red_mean": 0.22},
    )
    assert bad.looks_anomalous() is True


def test_base_empty_metrics_not_anomalous():
    """空 metrics -> 不异常 (向后兼容)。"""
    assert ExecutionResult(success=True, metrics={}).looks_anomalous() is False


def test_pie_mock_metrics_self_consistent():
    """Mock PIE 默认指标 (ndvi/nir/red 符号自洽) 通过锚点评测; kwargs 可覆盖构造反例。"""
    p = PIEEngineAdapter()
    r = p.execute(
        code="ndvi = (B8 - B4) / (B8 + B4)",
        credentials={"pie_token": "t"},
        region=BEIJING_BBOX,
    )
    assert r.success
    assert r.metrics["nir_mean"] == 0.35 and r.metrics["red_mean"] == 0.22
    assert check_anchors("", r.metrics).verdict == "ok"

    bad = p.execute(
        "x", {"pie_token": "t"}, BEIJING_BBOX,
        mock_ndvi_mean=-0.4, mock_nir_mean=0.35, mock_red_mean=0.22,
    )
    assert check_anchors("", bad.metrics).verdict == "hard_fail"


# ---------- 审查修复回归 (2026-08-14 对抗审查) ----------

def test_p3_mixed_endmembers_not_hard_fail():
    """P3 均值层面反号但物理合法的混合端元 (90%亮沙漠+10%浑浊水体) 不冤杀。

    (回归: P3 曾对任意反号 hard_fail, 但空间均值分母逐像素不同,
    混合区域均值反号是数学合法的——修复后只对深度负值(<=-0.1)启用。)
    """
    mixed = {
        "ndvi_mean": -0.034, "nir_mean": 0.812, "red_mean": 0.748,
        "valid_ratio": 0.98,
    }
    assert check_anchors("", mixed).verdict == "ok"


def test_r1_suspicious_not_hard_reject():
    """R1 区域先验命中 (仅 suspicious) 不得被 looks_anomalous 扁平化成硬拒。

    (回归: base.py 曾用 verdict != "ok" 把 suspicious 一并判异常,
    违反 anchors.json '先验仅提醒绝不硬拒' 契约。)
    """
    from src.platform.base import ExecutionResult

    m = {
        "ndvi_mean": 0.9, "nir_mean": 0.5, "red_mean": 0.1,
        "valid_ratio": 0.98,
        "region_bbox": BEIJING_BBOX, "season": "summer",
    }
    assert check_anchors("", m).verdict == "suspicious"  # 先验命中
    assert ExecutionResult(success=True, metrics=m).looks_anomalous() is False
