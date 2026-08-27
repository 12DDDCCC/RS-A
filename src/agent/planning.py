"""生成前计划节点 (设计蓝图 P1-4)。

把"选数据集/时间/方法"从 LLM 代码里的隐式决策变成显式 JSON 计划,
并做确定性三查 (不烧 LLM/配额就拦截"合法但错误"的选择):
  1. 数据集 Collection ID 在白名单且 _verified
  2. 计划年份与数据集 temporal_coverage 相交非空
     (防"2015-2025 时序选了 S2_SR"静默产出前两年全空的结果)
  3. 指数所需波段属于该数据集波段表

LLM 只负责"出计划"(需判断力), 校验全部确定性。
D=1 不变: 代码仍是一个脚本, 但它是计划的确定性展开。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.knowledge import load_knowledge
from src.codegen.validator import COLLECTION_WHITELIST, DATASET_BANDS

# 计划里合法的任务类型 (与 output/caption 的分类对齐)
TASK_TYPES = ("vegetation", "water", "land_cover", "change_detection")


@dataclass
class PlanReport:
    """计划校验报告。"""

    passed: bool
    issues: list[str] = field(default_factory=list)
    plan: dict = field(default_factory=dict)


def validate_plan(plan: dict) -> PlanReport:
    """确定性校验一个分析计划。

    plan 结构 (LLM 输出解析而来):
      {"dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
       "index": "NDVI", "bands": ["B8", "B4"],
       "years": [2020, 2024], "method": "中值合成"}
    """
    issues: list[str] = []

    ds = plan.get("dataset_id", "")
    kb = load_knowledge()

    # 1) 数据集白名单
    if ds not in COLLECTION_WHITELIST:
        issues.append(f"计划选用的数据集不在白名单: {ds!r} -> 请查 datasets.json")

    # 2) 时间窗与 temporal_coverage 相交 (LC08 备用 ID 用专属覆盖, 不误套 LC09 起点)
    years = plan.get("years") or []
    coverage = None
    for entry in kb["datasets"].values():
        if entry.get("gee_collection_id") == ds:
            coverage = entry.get("temporal_coverage")
            break
        if ds and entry.get("landsat8_collection_id") == ds:
            coverage = entry.get("landsat8_temporal_coverage")
            break
    if years and coverage and coverage.get("start"):
        start_year = int(str(coverage["start"])[:4])
        earliest = min(years)
        if earliest < start_year:
            issues.append(
                f"数据集 {ds} 自 {coverage['start']} 起才有数据, "
                f"计划从 {earliest} 年开始 -> 前几年将无数据"
            )

    # 3) 波段归属 (指数所需波段属于该数据集)
    bands = plan.get("bands") or []
    if ds in DATASET_BANDS and bands:
        allowed = DATASET_BANDS[ds]
        for b in bands:
            if b not in allowed:
                issues.append(
                    f"计划用到波段 {b}, 但数据集 {ds} 的波段表里没有 -> 数据集/波段错配"
                )

    return PlanReport(passed=not issues, issues=issues, plan=plan)


def plan_prompt_block(plan: dict) -> str:
    """把通过校验的计划拼进生成 prompt (计划是生成的宪法)。"""
    return (
        "# 分析计划 (已通过确定性校验, 逐条严格遵守)\n"
        f"数据集: {plan.get('dataset_id','')}\n"
        f"指数: {plan.get('index','')}\n"
        f"波段: {plan.get('bands',[])}\n"
        f"年份: {plan.get('years',[])}\n"
        f"方法: {plan.get('method','')}\n"
    )
