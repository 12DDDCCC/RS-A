# -*- coding: utf-8 -*-
"""遥感学科验证子 agent (G4): 确定性学科规则层, 抓"能跑但学科上算错"的代码。

多 agent 协同分工 (与既有防护不重复):
  validator 白名单  -> 防 API/数据集/波段名幻觉 (能不能引用)
  domain_validator  -> 防学科公式/缩放/方向错误 (算了对不对)   ← 本模块
  reviewer 子agent  -> LLM 视角语义审查 (软性意见)
  sandbox + anchors -> 实测兜底 (跑起来结果物理合理吗)

规则集 (每条都来自遥感学科标准定义, 与 datasets.json 的核实事实一致):
  R1 NDVI 方向: 分子必须 NIR-Red
  R2 S2 SR 反射率指数计算必须 ×0.0001 (DN->reflectance)
  R3 Landsat C2 L2 必须 DN×0.0000275-0.2
  R4 NDWI 用 Green+NIR; MNDWI 用 Green+SWIR (两者不可混)
  R5 NDBI 用 SWIR-NIR 方向
  R6 S1 雷达不做云掩膜 (无云), 用了 CLOUDY_PIXEL_PERCENTAGE 即错
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DomainReport:
    passed: bool
    issues: list[str] = field(default_factory=list)


def verify_domain(code: str) -> DomainReport:
    """对生成的 GEE 代码做遥感学科规则校验 (纯确定性, 零成本)。"""
    issues: list[str] = []
    low = code.lower()
    has_s2_sr = "s2_sr" in low or "copernicus/s2_sr" in low
    has_landsat = "landsat" in low

    # ---- R1 NDVI 方向: normalizedDifference 参数序 或 手写分式 ----
    if "normalizeddifference" in low:
        # normalizedDifference(["B8","B4"]) => NIR-Red ✓; 反之 ✗ (仅当两波段是已知 NIR/Red 对)
        for m in re.finditer(r"""normalizedDifference\(\s*\[['"]?([A-Za-z0-9_]+)['"]?\s*,\s*['"]?([A-Za-z0-9_]+)['"]?\s*\]\)""", code):
            a, b = m.group(1).upper(), m.group(2).upper()
            pair = {a, b}
            nir = {"B8", "B8A", "SR_B5", "B5"}
            red = {"B4", "SR_B4"}
            if len(pair) == 2 and pair <= nir | red:
                if a not in nir:
                    issues.append(
                        f"NDVI 方向错误: normalizedDifference(['{a}','{b}']) 分子必须是 NIR, "
                        f"当前 Red 在前"
                    )
    else:
        # 手写分式: (red - nir) 形态
        m = re.search(r"\(\s*\w*red\w*\s*-\s*\w*nir\w*\s*\)", low)
        if m:
            issues.append("NDVI 公式疑似反向: 检测到 (Red − NIR) 形态, 学科标准为 (NIR − Red)/(NIR + Red)")

    # ---- R2 S2 SR 缩放: 使用 B* 波段做计算但全文无 0.0001 ----
    if has_s2_sr and re.search(r"[\"']B\d", code):
        if "0.0001" not in code and "10000" not in code:
            # 允许 divide(10000) 写法
            if "divide(" not in low or "10000" not in code:
                issues.append(
                    "S2 SR 波段是 DN (0-10000): 计算反射率类指数前必须 ×0.0001 "
                    "(或 divide(10000)); NDVI 虽数学上可约, 但 METRICS 里的 nir_mean/"
                    "red_mean 会是 DN 量级, 锚点评测将判异常"
                )

    # ---- R3 Landsat C2 L2 缩放 ----
    if has_landsat and re.search(r"[\"']SR_B\d", code):
        if "0.0000275" not in code and "2.75e-05" not in code.lower():
            issues.append(
                "Landsat C2 L2 的 SR_B* 是 DN: 必须按 ×0.0000275−0.2 缩放为反射率, "
                "否则 METRICS 数值量级全错"
            )

    # ---- R4 NDWI vs MNDWI 波段对 (变量名定位 + 引号/大小写兼容) ----
    nd_pair_re = re.compile(
        r"(\w+)\s*=\s*[^\n]*?normalizedDifference\(\s*\[['\"]?([A-Za-z0-9_]+)['\"]?\s*,\s*['\"]?([A-Za-z0-9_]+)['\"]?\s*\]\)"
    )
    swir = {"B11", "B12", "SR_B6", "SR_B7", "B6", "B7"}
    nir_b = {"B8", "B8A", "SR_B5", "B5"}
    for m in nd_pair_re.finditer(code):
        var, a, b = m.group(1).lower(), m.group(2).upper(), m.group(3).upper()
        pair = {a, b}
        if "mndwi" in var and (pair & nir_b):
            issues.append("MNDWI 用 Green+SWIR, 检测到用了 NIR 波段 (那是 NDWI 的公式)")
        if ("ndwi" in var and "mndwi" not in var) and (pair & swir):
            issues.append("NDWI 用 Green+NIR, 检测到用了 SWIR 波段 (那是 MNDWI 的公式)")

    # ---- R5 NDBI 方向: (SWIR - NIR), NIR 在前即反 ----
    if "ndbi" in low:
        m = re.search(
            r"""normalizedDifference\(\s*\[['\"]?(B8|SR_B5|B5)['\"]?\s*,\s*['\"]?(B11|B12|SR_B6|B6)['\"]?\s*\]\)""",
            code, re.IGNORECASE,
        )
        if m:
            issues.append(
                f"NDBI 方向错误: normalizedDifference(['{m.group(1)}','{m.group(2)}']) "
                "分子必须 SWIR 在前 (SWIR − NIR)"
            )

    # ---- R6 S1 雷达做云掩膜 ----
    if "sentinel-1" in low or "sentinel1" in low or "s1_grd" in low:
        if "cloudy_pixel_percentage" in low or "cloud_score" in low:
            issues.append("Sentinel-1 是 SAR 雷达, 不受云影响也不含云量字段——检测到对 S1 做云过滤")

    return DomainReport(passed=len(issues) == 0, issues=issues)
