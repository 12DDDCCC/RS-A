"""PIE-Engine (航天宏图) 平台适配器。

重要: PIE-Engine 的 Python SDK 不开源,其 API (认证/初始化/数据集ID/波段命名)
必须登录 https://engine.piesat.cn 帮助中心核实后才能填实。
本文件目前提供接口骨架 + Mock 实现,标注所有需核实点。

禁令第6条: PIE API 盲猜 -> 未核实处必须标注 # TODO: 需登录核实,不臆造接口。

待小孟同学登录平台核实以下信息后回填:
  1. Python SDK 的 pip 包名 (疑似 `pie` 或 `pie-engine`,未核实)
  2. 认证方式: pie.Initialize(token=...) 的确切签名
  3. 数据集 Collection ID 命名规则 (见 datasets.json 的 pie_engine_datasets)
  4. 代码提交执行 + 取结果的 API
  5. 波段命名是否与 GEE 的 B4/B8 一致
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import hashlib
from pathlib import Path

from src.platform.base import ExecutionResult, RemoteSensingPlatform

# TODO: 需登录核实 - PIE SDK 的真实导入路径
# try:
#     import pie  # 未核实包名
# except ImportError:
#     pie = None


def _write_mock_jpeg(out_path: Path) -> None:
    """Mock 产物: 写一张真实的小 JPEG (灰度渐变图)。

    诚实模拟"执行成功并产出可查看的 JPEG"——输出层有 JPEG 魔数校验,
    占位文本字节会被拒绝, 所以 Mock 也必须产真图。此图不代表 PIE 真实结果。
    """
    import numpy as np
    from PIL import Image  # pillow 已在 requirements.txt 声明

    arr = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    Image.fromarray(arr, mode="L").save(out_path, format="JPEG", quality=85)


class PIEEngineAdapter(RemoteSensingPlatform):
    """PIE-Engine 适配器。

    当前为 Mock 实现: 不真正调用 PIE,而是模拟"代码执行成功并产出 JPEG",
    用于在 PIE API 核实前跑通 agent 主链路。
    """

    name = "pie-engine"

    def execute(self, code: str, credentials: dict, region: dict, **kwargs) -> ExecutionResult:
        """提交代码到 PIE-Engine 云端执行。

        TODO: 需登录核实 - 真实实现应:
          1. 用 credentials 初始化 (per-user, 不动全局 env)
          2. 提交 code 到云端
          3. 取结果 + 中间指标
        """
        # ----- Mock 实现 (PIE API 核实前的占位) -----
        if not self.test_connection(credentials):
            return ExecutionResult(
                success=False,
                error="PIE 凭证无效或未配置 (Mock 模式: 缺少 test_token)",
            )

        cache_dir = paths_cache_root()
        cache_dir.mkdir(exist_ok=True)
        # 用代码哈希做产物文件名,避免冲突 (不用 Date.now,可复现)
        digest = hashlib.sha1(f"{code}{region}".encode()).hexdigest()[:12]
        out_path = cache_dir / f"result_{digest}.jpg"
        _write_mock_jpeg(out_path)

        # 模拟中间指标: agent 据此判断是否纠错。
        # nir/red 默认值与 ndvi_mean=0.42 符号自洽 (0.35-0.22>0 且 0.42>0),
        # 供第四层防护 (锚点 P3 符号校验) 使用; 均可用 mock_* kwargs 覆盖。
        metrics = {
            "ndvi_mean": kwargs.get("mock_ndvi_mean", 0.42),
            "nir_mean": kwargs.get("mock_nir_mean", 0.35),
            "red_mean": kwargs.get("mock_red_mean", 0.22),
            "valid_ratio": kwargs.get("mock_valid_ratio", 0.95),
        }
        return ExecutionResult(success=True, output_path=str(out_path), metrics=metrics)

    def test_connection(self, credentials: dict) -> bool:
        """测试凭证是否有效。

        TODO: 需登录核实 - 真实实现调用 PIE 的认证校验。
        """
        # Mock: 只要有 token 就算通过
        return bool(credentials.get("test_token") or credentials.get("pie_token"))


# TODO: 需登录核实 - 以下为真实 PIE 调用的预期形态,注释保留,核实后启用:
#
# def _real_execute(self, code, credentials, region):
#     import pie
#     pie.Initialize(token=credentials["pie_token"])  # 待核实签名
#     # 待核实: 如何把 code 字符串提交执行并取结果
#     # 关键: credentials 用完即弃,不写日志,不存全局
