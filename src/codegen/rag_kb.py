"""RAG 知识库检索: 把相关的数据集/指数知识注入 LLM 提示词。

对应 arXiv 论文的解法 (r=0.87): 让 LLM "先查表再写代码", 把核实的波段/公式
作为上下文喂给生成器, 从源头降低幻觉。

MVP 用关键词匹配做检索 (简单可靠); 后续可升级为向量检索。
"""
from __future__ import annotations

import json

from src.knowledge import load_knowledge

_KB = load_knowledge()


def retrieve_for_task(task_keywords: list[str]) -> str:
    """根据任务关键词检索相关知识库片段, 返回提示词用的上下文字符串。

    Args:
        task_keywords: 如 ["植被", "变化"], ["水体"], ["土地覆盖"]

    Returns:
        结构化的知识库片段 (数据集+指数+注意事项), 喂给 LLM。
    """
    kb_text = ["# 遥感数据集知识库 (已官方文档核实,生成代码时必须使用以下定义,禁止臆造)\n"]

    # 按任务类型选数据集
    kw = " ".join(task_keywords)
    want_vegetation = any(k in kw for k in ("植被", "vegetation", "ndvi", "绿"))
    want_water = any(k in kw for k in ("水体", "water", "水", "淹没"))
    want_time = any(k in kw for k in ("变化", "时序", "time", "change"))

    def emit(ds_key: str):
        ds = _KB["datasets"][ds_key]
        kb_text.append(f"## {ds['name']}")
        kb_text.append(f"- Collection ID: {ds['gee_collection_id']}")
        # SAR (Sentinel-1) 无云量概念, 知识库即无此键 —— 不能无脑取
        cloud = ds.get("cloud_field")
        kb_text.append(f"- 云量字段: {cloud}" if cloud else "- 云量字段: 无 (SAR 雷达全天候, 不受云影响)")
        # scale_factor 可能为 null (S1 雷达 DN 无需辐射缩放) —— 键存在不代表可用
        if ds.get("scale_factor"):
            sf = ds["scale_factor"]
            if sf["operation"] == "multiply":
                kb_text.append(f"- 缩放: DN × {sf['value']}")
            else:
                kb_text.append(f"- 缩放: DN × {sf['scale']} − {-sf['offset']}")
        if "bands" in ds:
            bands_str = ", ".join(ds["bands"].keys())
            kb_text.append(f"- 波段: {bands_str}")
        if "key_indices" in ds:
            for idx, f in ds["key_indices"].items():
                kb_text.append(f"- {idx} = {f}")
        kb_text.append("")

    if want_vegetation:
        emit("sentinel2_sr")
        if want_time:
            emit("landsat9_c2_l2")
    if want_water:
        emit("sentinel2_sr")  # NDWI/MNDWI
        emit("sentinel1_grd")  # SAR 水体
    if not (want_vegetation or want_water):
        # 默认给主力数据集
        emit("sentinel2_sr")
        emit("landsat9_c2_l2")

    # 指数陷阱警示
    kb_text.append("## 指数定义 (同名异义陷阱)")
    for idx, info in _KB["indices_reference"].items():
        if idx.startswith("_"):
            continue
        # full_name/formula 等字段并非每个指数都有 (NDBI 新补录只有部分键) —— 全 .get 防御
        kb_text.append(f"- {idx}: formula={info.get('formula', info.get('landsat_formula', '?'))}"
                       f"{(' (' + info['full_name'] + ')') if info.get('full_name') else ''}"
                       f"  [{info.get('rule','')}]")

    kb_text.append("\n## 铁律")
    kb_text.append("- NDVI 分子必须是 NIR - Red, 不可反向")
    kb_text.append("- 云量字段不可跨数据集互换 (S2=CLOUDY_PIXEL_PERCENTAGE, Landsat=CLOUD_COVER)")
    kb_text.append("- NDWI (Green,NIR) 与 MNDWI (Green,SWIR) 是两个公式, 不可混用")

    return "\n".join(kb_text)


def get_dataset_json_for_llm() -> str:
    """返回完整 datasets.json 文本 (备用: 直接全量注入)。"""
    return json.dumps(_KB, ensure_ascii=False, indent=2)
