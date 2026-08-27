"""平台抽象层基类。

把"提交遥感代码到云端执行"这个动作抽象成统一接口,隔离 PIE-Engine / GEE 的差异。
设计要点 (对应 grill 禁令):
  - 每次 execute 必须注入 per-user 凭证,绝不依赖全局 env (防多用户串号)
  - 只接受"代码字符串"作为输入,云端算,只返回"结果路径/数据" (不下载原始影像)
  - 平台无关,PIE/GEE 适配器各自实现

LLM 生成代码 → 提交到这里 → 云端执行 → 取结果。这是 D=1 模式的执行底座。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExecutionResult:
    """单次代码执行的返回。"""

    success: bool
    """是否执行成功。"""

    output_path: str | None = None
    """结果产物路径 (JPEG/临时文件),失败时为 None。"""

    metrics: dict = field(default_factory=dict)
    """执行中间指标,供 agent 看一眼判断是否要纠错 (如 NDVI 均值/有效像素比例)。"""

    error: str | None = None
    """失败时的错误信息 (不包含任何凭证,凭证绝不落此处)。"""

    def looks_anomalous(self) -> bool:
        """agent 用来判断中间结果是否异常 → 触发纠错。

        第四层防护 (P1-1): 委托 src.codegen.anchors.check_anchors 做物理锚点评测
        (P1 值域硬界 / P3 符号一致性 / R1 区域先验)。
        旧启发式 (valid_ratio 过低 / ndvi<-0.5) 保留为兜底, 防护只增不减:
        P3 是旧阈值的增强 (能抓 -0.4 的反向 NDVI), 但无 nir/red 指标时仍靠旧兜底。
        """
        if not self.success:
            return True
        if not self.metrics:
            return False  # 向后兼容: 无指标不冤判
        # 通用质量门: 有效像素过低 (全云/全掩膜)
        if self.metrics.get("valid_ratio", 1.0) < 0.3:
            return True
        # 旧兜底: NDVI 深度负值 (典型: 波段选反或全云)
        ndvi_mean = self.metrics.get("ndvi_mean")
        if ndvi_mean is not None and ndvi_mean < -0.5:
            return True
        # 锚点评测 (延迟导入, 防止 platform -> codegen -> platform 循环依赖)。
        # 只对 hard_fail 判异常: R1 区域先验契约是"仅 suspicious 级提醒, 绝不
        # 硬拒"(anchors.json _meta), 先验是近似值, 硬拒会冤杀合法结果。
        from src.codegen.anchors import check_anchors

        return check_anchors("", self.metrics).verdict == "hard_fail"


class RemoteSensingPlatform(ABC):
    """遥感云平台的平台无关接口。"""

    name: str = "base"

    @abstractmethod
    def execute(self, code: str, credentials: dict, region: dict, **kwargs) -> ExecutionResult:
        """提交一段遥感处理代码到云端执行。

        Args:
            code: LLM 生成的平台特定处理代码 (Python/JS 字符串)。
            credentials: per-user 凭证 (token/key),由调用方注入,绝不来自全局 env。
            region: 目标区域 {lon_min, lat_min, lon_max, lat_max}。
            **kwargs: 分辨率、时间窗等。

        Returns:
            ExecutionResult,包含结果路径和供纠错判断的中间指标。

        安全: 实现方必须保证 credentials 不进日志、不进 error 字段、
              不被缓存到全局状态。
        """
        ...

    @abstractmethod
    def test_connection(self, credentials: dict) -> bool:
        """用给定凭证测试连接是否可用 (认证校验)。"""
        ...
