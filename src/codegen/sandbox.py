"""第三层防护: 沙箱试跑。

代码通过前两层后, 在极小区域 + 低分辨率 + 短时间窗内试跑一遍,
验证"真能跑 + 结果合理"才提交全量。对应 grill 禁令: 防止烧光用户配额。

沙箱约束:
  - 区域缩到极小 (如 0.01° × 0.01°)
  - 分辨率降低 (如 100m+)
  - 时间窗缩到几天
  - 超时保护
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from src.platform import RemoteSensingPlatform, get_platform
from src.platform.base import ExecutionResult


@dataclass
class SandboxConfig:
    """沙箱试跑的约束参数。"""

    coarse_scale_m: int = 500      # 分辨率降到 500m
    short_window_days: int = 7     # 时间窗缩到 7 天
    timeout_s: int = 60            # 试跑超时 (超时即拒绝全量执行)


def sandbox_trial(
    code: str,
    user_id: str,
    region: dict,
    platform: RemoteSensingPlatform | None = None,
    config: SandboxConfig | None = None,
) -> ExecutionResult:
    """在约束条件下试跑代码。

    把用户请求的大区域/高分辨率/长时间窗, 压缩成沙箱参数试跑。
    成功且结果不异常, 才允许全量执行。

    凭证流 (P0-5): 只收 user_id, 调用瞬间解密, 传完即弃, 不进 state。
    """
    config = config or SandboxConfig()
    # 平台选择与 nodes.py 同源 (env 控制, 默认 gee; 测试由 conftest 强制 pie mock)
    import os as _os

    platform = platform or get_platform(_os.environ.get("REMOTE_SENSING_PLATFORM", "gee"))

    # 执行瞬间解密凭证 (不在 state 里流转)
    from src.io.credentials import load_credentials

    try:
        credentials = load_credentials(user_id)
    except FileNotFoundError:
        return ExecutionResult(success=False, error="[沙箱] 用户凭证未配置")
    except RuntimeError as e:
        return ExecutionResult(success=False, error=f"[沙箱] 凭证服务不可用: {e}")

    # 缩小区域: 取中心点附近一小块
    # GAUL 区县模式 (region=None): roi 由代码从 GAUL 动态解析, 无中心点可缩,
    # 试跑原样执行 (超时保护照旧); GAUL 表查询本身轻量。
    if region is None:
        sandbox_region = None
    else:
        cx = (region["lon_min"] + region["lon_max"]) / 2
        cy = (region["lat_min"] + region["lat_max"]) / 2
        half = 0.01  # 约 1km
        sandbox_region = {
            "lon_min": cx - half,
            "lat_min": cy - half,
            "lon_max": cx + half,
            "lat_max": cy + half,
        }

    # 试跑, 带真实超时保护 (Mock 平台下直接执行; 真实平台需在 code 里注入缩放参数)。
    # 超时用线程池实现: 超时后不再等待执行线程, 直接拒绝全量执行。
    # GAUL 区县模式 (region=None) 代码需出网解析行政区边界 (GAUL filter +
    # computePixels 串行), 试跑耗时天然更高 —— 超时放宽 3 倍 (防护只增不减
    # 精神下的适配: 超时保护仍在, 只是给边界解析留足时间)。
    timeout_s = config.timeout_s if region is not None else config.timeout_s * 3
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(
        platform.execute,
        code=code,
        credentials=credentials,
        region=sandbox_region,
        scale=config.coarse_scale_m,
        window_days=config.short_window_days,
        _sandbox=True,
    )
    try:
        result = future.result(timeout=timeout_s)
    except FuturesTimeout:
        return ExecutionResult(
            success=False,
            error=f"[沙箱] 试跑超时 ({timeout_s}s), 拒绝全量执行",
        )
    except Exception as e:
        # 执行器异常 (网络错/适配器抛错) 也转为失败结果, 不裸穿成 500
        return ExecutionResult(success=False, error=f"[沙箱] 试跑异常: {e}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if not result.success:
        result.error = f"[沙箱] {result.error or '执行失败'}"
    elif result.looks_anomalous():
        result.error = "[沙箱] 结果异常 (可能波段反/全云), 拒绝全量执行"
        result.success = False

    return result
