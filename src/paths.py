"""运行时数据根目录的单一事实源。

- 源码运行: 仓库根 (src/paths.py 的 parents[1])
- PyInstaller frozen: exe 所在目录 (便携式, 数据随 exe 走)

8 个模块 (jobs/sessions/obs/auth/failure_store/output/pie_adapter/credentials)
统一经 cache_root() 取 cache/, 禁止再各自 Path(__file__).parents 推导 ——
frozen 下那会落进 _internal 依赖目录 (obsidian 37 事故: jobs.db 写进
_internal, 用户当垃圾清理后数据丢失)。
"""
from __future__ import annotations

import sys
from pathlib import Path


def data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def cache_root() -> Path:
    return data_root() / "cache"
