"""数据集知识库加载器。

提供对 datasets.json 的只读访问,供 codegen (RAG/校验) 和测试使用。
所有数据来自 GEE 官方文档核实,详见 datasets.json 的 _meta。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATASETS_PATH = Path(__file__).parent / "datasets.json"


@lru_cache(maxsize=1)
def load_knowledge() -> dict:
    """加载并缓存数据集知识库。"""
    with open(_DATASETS_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_dataset(name: str) -> dict:
    """按名称取单个数据集定义,找不到抛 KeyError。"""
    return load_knowledge()["datasets"][name]


def list_datasets() -> list[str]:
    """列出所有可用数据集名称。"""
    return list(load_knowledge()["datasets"].keys())


def index_formula(dataset: str, index: str) -> str:
    """取指定数据集上某指数的公式字符串 (如 sentinel2_sr 的 NDVI)。"""
    return get_dataset(dataset)["key_indices"][index]
