"""第四层防护: 锚点评测 (结果物理合理性)。

前三层拦"代码写错/跑不起来", 这一层拦"跑出来了但结果物理不可能":
  - P1 值域硬界 (hard_fail): 归一化指数均值 (nd*_mean) 必须在 [-1, 1]
  - P3 符号一致性 (hard_fail): sign(ndvi_mean) == sign(nir_mean - red_mean)
  - R1 区域先验 (仅 suspicious): 锚点区 NDVI 季节区间提醒 (knowledge/anchors.json)

评审核实过的教训 (设计定稿, 勿加回):
  - NDVI 对线性缩放不敏感, "忘乘 0.0001"用值域判不住 -> 不设反射率规则
  - P3 是旧阈值 (ndvi < -0.5) 的增强: -0.4 的反向 NDVI 旧阈值漏掉, P3 能抓
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_ANCHORS_JSON = Path(__file__).resolve().parents[1] / "knowledge" / "anchors.json"


@dataclass
class AnchorReport:
    """锚点评测结论。"""

    verdict: str = "ok"
    """判定: "hard_fail" (物理不可能, 必须纠错) | "suspicious" (先验提醒) | "ok"。"""

    hits: list[dict] = field(default_factory=list)
    """命中规则列表, 每条 {rule_id, severity, observed, expected, hint}。"""


def check_anchors(task_type: str, metrics: dict) -> AnchorReport:
    """对执行中间指标做物理合理性评测。

    Args:
        task_type: 任务类型 (当前规则与任务无关, 留作扩展入口)。
        metrics: 执行中间指标。可选键 region_bbox (4 键 bbox) + season
            ("summer"/"winter") 两者齐备才启用 R1 区域先验。

    判定: 任一 hard_fail 命中 -> hard_fail; 否则任一命中 -> suspicious; 否则 ok。
    """
    hits: list[dict] = []
    hits += _rule_p1_index_range(metrics)
    hits += _rule_p3_ndvi_sign(metrics)
    hits += _rule_r1_region_prior(metrics)

    if any(h["severity"] == "hard_fail" for h in hits):
        return AnchorReport(verdict="hard_fail", hits=hits)
    if hits:
        return AnchorReport(verdict="suspicious", hits=hits)
    return AnchorReport(verdict="ok", hits=hits)


def _num(v) -> float | None:
    """取数值; 布尔/字符串/None 一律视为缺失, 不冤判。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _rule_p1_index_range(metrics: dict) -> list[dict]:
    """P1 值域硬界: 归一化指数均值 (ndvi_mean/ndwi_mean...) 必须在 [-1, 1]。

    只匹配 nd 前缀 + _mean 后缀的归一化指数, 不碰反射率类指标。
    """
    hits = []
    for key, value in metrics.items():
        k = key.lower()
        if not (k.startswith("nd") and k.endswith("_mean")):
            continue
        v = _num(value)
        if v is not None and abs(v) > 1.0:
            hits.append({
                "rule_id": f"P1_index_range:{key}",
                "severity": "hard_fail",
                "observed": v,
                "expected": "[-1, 1]",
                "hint": f"{key}={v} 超出归一化指数值域, 物理不可能 (疑似缩放系数/单位错)",
            })
    return hits


def _rule_p3_ndvi_sign(metrics: dict) -> list[dict]:
    """P3 符号一致性: sign(ndvi_mean) 必须 == sign(nir_mean - red_mean)。

    逐像素恒同号 (分母 nir+red>0); 但空间均值层面分母逐像素不同, 混合端元
    (如 90% 亮沙漠 + 10% 浑浊水体) 的均值反号是物理合法的——故只对
    深度负 NDVI (<= -0.1) 且反号时才 hard_fail: 波段选反的真特征是深层
    负值, 近零反号几乎必是混合端元均值效应。|nir-red| < 0.01 跳过防噪声。
    """
    ndvi = _num(metrics.get("ndvi_mean"))
    nir = _num(metrics.get("nir_mean"))
    red = _num(metrics.get("red_mean"))
    if ndvi is None or nir is None or red is None:
        return []
    diff = nir - red
    if abs(diff) < 0.01:
        return []
    if ndvi > -0.1:  # 近零: 混合端元均值效应区间, 不冤判
        return []
    if (ndvi >= 0) != (diff >= 0):
        return [{
            "rule_id": "P3_ndvi_sign",
            "severity": "hard_fail",
            "observed": {"ndvi_mean": ndvi, "nir_minus_red": round(diff, 4)},
            "expected": "sign(ndvi_mean) == sign(nir_mean - red_mean) (深度负值)",
            "hint": "NDVI 深度负且与 (近红外-红) 反号, 疑似 NIR/RED 波段选反",
        }]
    return []


@lru_cache(maxsize=1)
def _load_anchor_regions() -> tuple[dict, ...]:
    """读锚点先验表; 文件缺失/损坏时返回空元组 (R1 自动降级, 不崩主链路)。"""
    try:
        data = json.loads(_ANCHORS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(data.get("anchor_regions", []))


def _rule_r1_region_prior(metrics: dict) -> list[dict]:
    """R1 区域先验 (仅 suspicious): 锚点区 NDVI 超季节先验区间 -> 提醒人工复核。

    先验是保守宽区间的常识估计 (anchors.json 全部标 approximate_prior),
    只做 suspicious 级提醒, 绝不 hard_fail。
    需 metrics 同时提供 region_bbox (取中心点匹配锚点) 与 season, 缺一即跳过。
    """
    ndvi = _num(metrics.get("ndvi_mean"))
    bbox = metrics.get("region_bbox")
    season = metrics.get("season")
    if ndvi is None or not isinstance(bbox, dict) or season not in ("summer", "winter"):
        return []
    try:
        cx = (bbox["lon_min"] + bbox["lon_max"]) / 2
        cy = (bbox["lat_min"] + bbox["lat_max"]) / 2
    except (KeyError, TypeError):
        return []

    hits = []
    for anchor in _load_anchor_regions():
        ab = anchor.get("bbox", {})
        if not (ab.get("lon_min", 1e9) <= cx <= ab.get("lon_max", -1e9)
                and ab.get("lat_min", 1e9) <= cy <= ab.get("lat_max", -1e9)):
            continue
        rng = anchor.get(f"ndvi_{season}_range")
        if not (isinstance(rng, list) and len(rng) == 2):
            continue
        lo, hi = rng
        if not (lo <= ndvi <= hi):
            hits.append({
                "rule_id": f"R1_region_prior:{anchor.get('name', '?')}",
                "severity": "suspicious",
                "observed": ndvi,
                "expected": f"{anchor.get('name')} {season} NDVI 先验 [{lo}, {hi}] (approximate)",
                "hint": f"{anchor.get('_note', '先验为近似估计')}; 请人工复核区域/季节/数据",
            })
    return hits
