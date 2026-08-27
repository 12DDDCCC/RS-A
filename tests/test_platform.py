"""平台抽象层测试。

验证:
  - 工厂能取到适配器, 默认 PIE
  - per-user 凭证注入 (不依赖全局)
  - Mock PIE 能产出结果 + 中间指标
  - 异常检测逻辑 (looks_anomalous) 正确触发纠错
"""
from __future__ import annotations

from src.platform import get_platform
from src.platform.base import ExecutionResult


def test_factory_returns_pie_by_default():
    p = get_platform("pie-engine")
    assert p.name == "pie-engine"
    # 未知名也回落到 PIE
    assert get_platform("unknown").name == "pie-engine"


def test_pie_mock_executes_with_credentials():
    """Mock PIE: 有凭证 -> 执行成功, 产出 JPEG, 带中间指标。"""
    p = get_platform("pie-engine")
    result = p.execute(
        code="print('ndvi')",
        credentials={"pie_token": "user_A_token"},  # per-user 注入
        region={"lon_min": 116, "lat_min": 39, "lon_max": 117, "lat_max": 40},
    )
    assert result.success is True
    assert result.output_path.endswith(".jpg")
    assert "ndvi_mean" in result.metrics


def test_credentials_per_user_isolation():
    """凭证按请求注入, 不存全局状态 -> 两个用户互不干扰。"""
    p = get_platform("pie-engine")
    r_a = p.execute("c", {"pie_token": "A"}, {"lon_min": 0, "lat_min": 0, "lon_max": 1, "lat_max": 1})
    r_b = p.execute("c", {"pie_token": "B"}, {"lon_min": 0, "lat_min": 0, "lon_max": 1, "lat_max": 1})
    # 适配器实例无全局凭证状态, 每次都从入参取
    assert r_a.success and r_b.success
    assert not hasattr(p, "_last_credentials")  # 不缓存凭证


def test_anomalous_result_triggers_correction_flag():
    """NDVI 均值异常为负 -> looks_anomalous 返回 True -> agent 应纠错。"""
    bad = ExecutionResult(success=True, metrics={"ndvi_mean": -0.8, "valid_ratio": 0.9})
    assert bad.looks_anomalous() is True


def test_normal_result_not_anomalous():
    good = ExecutionResult(success=True, metrics={"ndvi_mean": 0.42, "valid_ratio": 0.95})
    assert good.looks_anomalous() is False


def test_low_valid_ratio_is_anomalous():
    """有效像素过低 (如全云) -> 异常。"""
    cloudy = ExecutionResult(success=True, metrics={"valid_ratio": 0.1})
    assert cloudy.looks_anomalous() is True


def test_gee_adapter_exists_as_reference():
    """GEE 作为可核实参考适配器存在。"""
    p = get_platform("gee")
    assert p.name == "gee"
