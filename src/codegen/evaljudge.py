"""评测裁判 (P1-2): 用确定性规则判定代码是否符合评测用例期望。

与三层防护不同, 这是对生成结果的"赛后评分":
  - 复用 validator 的提取逻辑 (used_datasets / used_bands / 白名单)
  - 在此之上叠加用例级期望: 数据集 ⊆ expect.datasets, 波段 ⊆ expect.bands,
    归一化指数方向符合 expect.index_direction
全确定性 (AST/正则), 零 LLM 成本, 评测可复现。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from src.codegen.validator import validate_code

# 波段 -> 光谱角色 (方向判定的事实依据, 对齐 datasets.json 波长表)
BAND_ROLE: dict[str, str] = {
    "B8": "NIR", "B8A": "NIR", "SR_B5": "NIR",
    "B4": "RED", "SR_B4": "RED",
    "B3": "GREEN", "SR_B3": "GREEN",
    "B11": "SWIR", "B12": "SWIR", "SR_B6": "SWIR",
}

# index_direction -> 允许的 (首项角色, 次项角色) 组合
# NIR_minus_Red: NDVI 分子 NIR 在前; Green_minus_NIR_or_SWIR: NDWI/MNDWI 分子 Green 在前
DIRECTION_RULES: dict[str, set[tuple[str, str]]] = {
    "NIR_minus_Red": {("NIR", "RED")},
    "Green_minus_NIR_or_SWIR": {("GREEN", "NIR"), ("GREEN", "SWIR")},
}

# 形态1: normalizedDifference(['B8', 'B4']) -> 首项是分子
_ND_CALL = re.compile(
    r"normalizedDifference\s*\(\s*\[\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\]"
)
# 形态2: (X - Y) / (X + Y) (反向引用保证分子分母同名同序)
_ND_ARITH = re.compile(
    r"\(\s*(\w+)\s*-\s*(\w+)\s*\)\s*/\s*\(\s*\1\s*\+\s*\2\s*\)"
)


@dataclass
class EvalVerdict:
    """评测判定结果。"""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    """不通过的具体原因 (供评测报告聚合)。"""


def judge_code(code: str, case: dict) -> EvalVerdict:
    """判定代码是否符合评测用例期望。

    Args:
        code: 被评代码 (内联 fixture 或真实生成产物)。
        case: evalset/cases.json 中的一条用例。

    Returns:
        EvalVerdict。passed=True 表示数据集/波段/指数方向全部符合期望。
    """
    expect = case["expect"]
    reasons: list[str] = []

    # 1) 语法必须可解析
    try:
        ast.parse(code)
    except SyntaxError as e:
        return EvalVerdict(False, [f"语法错误 (line {e.lineno}): {e.msg}"])

    # 2) 复用 validator: 白名单 + 数据集/波段交叉校验 + 提取 used_*
    report = validate_code(code)
    if not report.passed:
        # 幻觉 ID/波段/错配在白名单层已拒, 直接引用其确定性 issues
        return EvalVerdict(False, list(report.issues))

    # 3) 数据集必须落在用例期望集合内
    bad_ds = [d for d in dict.fromkeys(report.used_datasets) if d not in set(expect["datasets"])]
    if bad_ds:
        reasons.append(f"数据集 {bad_ds} 不在该用例期望集合内: {expect['datasets']}")

    # 4) 波段必须落在该任务期望波段子集内 (掩膜/分类波段全局放行:
    #    SCL/QA60 去云是知识库认可的合法用法, expect.bands 只圈任务光谱波段)
    from src.knowledge import load_knowledge

    mask_bands: set[str] = set()
    for entry in load_knowledge()["datasets"].values():
        mask_bands.update(entry.get("mask_bands", []))
    bad_bands = [
        b for b in dict.fromkeys(report.used_bands)
        if b not in set(expect["bands"]) and b not in mask_bands
    ]
    if bad_bands:
        reasons.append(f"波段 {bad_bands} 不在该用例期望波段子集内: {expect['bands']}")

    # 5) 归一化指数方向 (normalizedDifference 调用形态 + 算术式形态都抓)
    ok_pairs = DIRECTION_RULES.get(expect["index_direction"], set())
    for a, b in _norm_diff_pairs(code):
        ra, rb = BAND_ROLE.get(a), BAND_ROLE.get(b)
        if ra and rb and (ra, rb) not in ok_pairs:
            reasons.append(
                f"归一化指数方向错误: ({a} - {b}) 实际为 ({ra} - {rb}), "
                f"该用例期望 {expect['index_direction']}"
            )

    return EvalVerdict(passed=not reasons, reasons=reasons)


def _norm_diff_pairs(code: str) -> list[tuple[str, str]]:
    """提取代码中所有归一化差分的 (首波段, 次波段) 对 (两种形态都抓)。"""
    pairs = [(m.group(1), m.group(2)) for m in _ND_CALL.finditer(code)]
    pairs += [(m.group(1), m.group(2)) for m in _ND_ARITH.finditer(code)]
    return pairs
