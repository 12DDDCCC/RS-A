"""第一层防护: 白名单校验 (确定性, 抓事实幻觉)。

arXiv 论文实测 LLM 生成 GEE 代码的头号死因是 "API 名称幻觉 + 数据集选错"。
LLM 自查抓不住这类错 (子 agent 也天生偏盲), 故用确定性的白名单校验:
  代码里出现的数据集 Collection ID 和波段名, 必须在知识库核实过的白名单内。

这是 D=1 自由脚本的安全底座: 零幻觉、零 LLM 成本。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from src.knowledge import load_knowledge

# 知识库核实过的合法 Collection ID 白名单
def _build_whitelist() -> tuple[set[str], set[str], dict[str, set[str]]]:
    kb = load_knowledge()
    collection_ids: set[str] = set()
    band_names: set[str] = set()
    dataset_bands: dict[str, set[str]] = {}
    for ds in kb["datasets"].values():
        cid = ds.get("gee_collection_id")
        if cid and ds.get("_verified"):
            collection_ids.add(cid)
            if ds.get("bands"):
                dataset_bands[cid] = set(ds["bands"].keys())
            # 同族备用 Collection ID: landsat8_collection_id (旧字段, LC08 复用
            # LC09 波段表) + alt_collection_ids 通用列表 (GAUL 父路径/简化变体
            # 等官方同族入口) —— 均复用主波段表, 防止同族入口被误拒
            alt_cids = []
            if ds.get("landsat8_collection_id"):
                alt_cids.append(ds["landsat8_collection_id"])
            alt_cids.extend(ds.get("alt_collection_ids", []))
            for alt_cid in alt_cids:
                collection_ids.add(alt_cid)
                if cid in dataset_bands:
                    dataset_bands[alt_cid] = set(dataset_bands[cid])
            # 掩膜/分类波段 (SCL/QA60, 官方目录无物理波长故在知识库单列一表)
            # 同属本数据集的合法波段, 并入波段表供交叉校验
            # (下划线开头的是知识库元数据说明, 不是波段)
            if ds.get("mask_bands"):
                masks = {b for b in ds["mask_bands"] if not b.startswith("_")}
                dataset_bands.setdefault(cid, set()).update(masks)
        for b in ds.get("bands", {}):
            band_names.add(b)
        band_names.update(
            b for b in ds.get("mask_bands", {}) if not b.startswith("_")
        )
    # 常见指数名也加入 (不是波段, 但代码里合法)
    band_names |= {"VV", "VH"}
    # 指数名 (indices_reference) 也是合法标识: select("NDVI/NDBI") 斜杠
    # 组合重命名是 GEE 惯用法, 段可能是指数而非波段 (G3 实测误伤案例)
    for idx in kb.get("indices_reference", {}):
        if not idx.startswith("_"):
            band_names.add(idx)
    return collection_ids, band_names, dataset_bands

COLLECTION_WHITELIST, BAND_WHITELIST, DATASET_BANDS = _build_whitelist()


@dataclass
class ValidationReport:
    """白名单校验报告。"""

    passed: bool
    issues: list[str] = field(default_factory=list)
    used_datasets: list[str] = field(default_factory=list)
    used_bands: list[str] = field(default_factory=list)


def validate_code(code: str) -> ValidationReport:
    """校验 LLM 生成的代码: 语法 + 数据集ID + 波段名白名单。

    任何一项不通过 -> passed=False, 上层应让 LLM 重写。
    """
    issues: list[str] = []
    used_ds: list[str] = []
    used_bands: list[str] = []

    # 1) 语法层: 必须能解析 (Python 代码)
    try:
        ast.parse(code)
    except SyntaxError as e:
        issues.append(f"语法错误 (line {e.lineno}): {e.msg}")
        # 语法错就不再继续, 后续提取无意义
        return ValidationReport(passed=False, issues=issues)

    # 2) 数据集 Collection ID 校验: 提取字符串字面量里像 ID 的
    #    GEE 集合 ID 形如 COPERNICUS/S2_SR_HARMONIZED, LANDSAT/LC09/C02/T1_L2
    renamed_bands: set[str] = set()
    id_pattern = re.compile(r"[A-Z][A-Z0-9_]+(?:/[A-Z0-9_]+)+")
    for match in id_pattern.finditer(code):
        cid = match.group()
        # GEE 波段组合惯用法: select("B8/B4/B3") 斜杠拼波段名 (选择+重命名)。
        # 每段都是合法波段名 -> 是波段组合不是 Collection ID; 各段照走波段校验
        # (真实 LLM 代码实测误伤案例, M3 用此语法; 防护只增不减)
        segments = cid.split("/")
        if all(seg in BAND_WHITELIST for seg in segments):
            # 斜杠段是 select+rename 的自建指数名 (如 "NDVI/MNDWI"), 非数据集
            # 原生波段 -> 记入豁免集, 不参加第4步交叉校验 (实测误伤案例:
            # M3 土地覆盖任务用 select("NDVI/MNDWI/NDBI") 被判波段错配)
            renamed_bands.update(segments)
            continue
        used_ds.append(cid)
        if cid not in COLLECTION_WHITELIST:
            issues.append(
                f"数据集 ID 不在白名单: {cid} -> 可能是幻觉,请查 datasets.json 核实"
            )

    # 3) 波段名校验: 提取 .select('XX') 或 normalizedDifference(['XX','XX']) 里的波段名
    #    B\d{1,2}A? 抓 B8A(S2 窄近红外); SCL/QA60 是 S2 掩膜/分类波段
    band_pattern = re.compile(r"[\"']([BS][RV]_\w*\d+|B\d{1,2}A?|SCL|QA60|VV|VH)[\"']")
    for match in band_pattern.finditer(code):
        band = match.group(1)
        used_bands.append(band)
        if band not in BAND_WHITELIST:
            issues.append(f"波段名不在白名单: {band} -> 可能是幻觉")

    # 4) 交叉校验: 代码所用波段必须属于代码中实际使用的数据集。
    #    仅对知识库里有波段表的数据集生效 (防 Landsat 配 S2 的 B8 这类错配);
    #    若同时用了"白名单内但无波段表"的数据集 (如 S2 TOA), 波段归属未知,
    #    放宽处理防误伤合法多源代码。
    used_ids = set(used_ds)
    # 指数名同为计算产物, 与原生波段无关, 一并豁免交叉校验
    index_names = {i for i in BAND_WHITELIST
                   if i in ("NDVI", "NDWI", "MNDWI", "NDSI", "NDBI", "NBR")}
    renamed_bands |= index_names
    used_known = [cid for cid in used_ids if cid in DATASET_BANDS]
    has_unknown_bands = any(
        cid in COLLECTION_WHITELIST and cid not in DATASET_BANDS for cid in used_ids
    )
    if used_known and used_bands and not has_unknown_bands:
        allowed = set().union(*(DATASET_BANDS[c] for c in used_known))
        for band in set(used_bands):
            if band in renamed_bands:
                continue
            if band not in allowed:
                issues.append(
                    f"波段 {band} 不属于代码所用数据集 {used_known} -> 数据集/波段错配"
                )

    return ValidationReport(
        passed=len(issues) == 0,
        issues=issues,
        used_datasets=used_ds,
        used_bands=used_bands,
    )
