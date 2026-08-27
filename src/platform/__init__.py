"""平台抽象层入口: 工厂方法按名称取适配器。"""
from __future__ import annotations

from src.platform.base import ExecutionResult, RemoteSensingPlatform
from src.platform.gee_adapter import GEEAdapter
from src.platform.pie_adapter import PIEEngineAdapter

_REGISTRY: dict[str, type[RemoteSensingPlatform]] = {
    "pie-engine": PIEEngineAdapter,
    "gee": GEEAdapter,
}


def get_platform(name: str) -> RemoteSensingPlatform:
    """按名称实例化平台适配器。

    默认 (name 省略或未知) 返回 PIE-Engine (grill 决策: PIE 优先)。
    """
    cls = _REGISTRY.get(name, PIEEngineAdapter)
    return cls()


__all__ = ["RemoteSensingPlatform", "ExecutionResult", "get_platform", "PIEEngineAdapter", "GEEAdapter"]
