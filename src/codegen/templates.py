# -*- coding: utf-8 -*-
"""O1 模板管线: 高频任务类型固化确定性代码模板, LLM 只填参数。

收益 (obsidian 25 实测对照): token ~50k→<2k/任务, 成功率→~100%, 耗时 3-6分→~90秒。
模板代码基于南京参考实现 (nj5y_reference.py) 的实测形态, 走与 LLM 路径
完全相同的四层防护 (validator/domain/sandbox; reviewer 对确定性代码无意义故跳过)。

匹配失败 (意图不在模板集/参数缺失) 返回 None, 上层回退 LLM 自由生成 ——
防护只增不减, 长尾任务仍走原路径。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 单年土地覆盖模板: Sentinel-2 夏季中值 + 三指数 (NDVI/MNDWI/NDBI) +
# 六类确定性分类出图 (离散色块 + Patch 图例, 中文字体缺失退化英文)。
# 形态即 nj5y_reference.py 实测跑通版本; {year}/{cloud} 由参数填充。
# 2026-09-01 多期扩展 (landcover_change_v1): 『近N年』渲染 1×N 期对比图 +
# METRICS.landcover_change_pct 变化统计; 沙箱只试跑第一期。
# 三挡下载规格 (GEE computePixels 单请求上限 48MB; 三波段 12B/px 预算 3.3M px,
# 宽幅区域 max 挡实际宽度由预算式决定, ~2000 级):
_TIER_WIDTH = {"standard": 768, "high": 2048, "max": 2816}
_LANDCOVER_TEMPLATE = '''
import io
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

years = [{years_list}]
if SANDBOX:
    years = years[:1]   # 沙箱只验证第一期逻辑, 全期必超时
quality_tier = "{quality_tier}"
roi = ee.Geometry.Rectangle([REGION["lon_min"], REGION["lat_min"],
                             REGION["lon_max"], REGION["lat_max"]])
results = {{}}
panels = {{}}            # year -> 分类数组 (2D)
panel_w = {{}}
panel_h = {{}}
span_x = REGION["lon_max"] - REGION["lon_min"]
span_y = REGION["lat_max"] - REGION["lat_min"]
for year in years:
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(roi)
           .filterDate(year + "-07-01", year + "-08-31")
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", {cloud})))
    if col.size().getInfo() == 0:
        raise ValueError("该时段无影像, 请放宽时间范围或云量阈值")
    # 校正流程① 逐像元云掩膜: SCL 剔除 {{3 云影, 8/9 云, 10 卷云}},
    # 必须在 median 合成之前 (先掩云后合成; filterBounds 裁剪已在最前)
    def _mask_cloud(img):
        scl = img.select("SCL")
        return img.updateMask(
            scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)))
    img = col.map(_mask_cloud).median()
    # 校正流程② 辐射定标: SR 缩放 (S2 L2A 大气校正已在数据生产端完成)
    scaled = img.select(["B4", "B3", "B8", "B11"]).multiply(0.0001)
    ndvi = scaled.normalizedDifference(["B8", "B4"]).rename("NDVI")
    mndwi = scaled.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    ndbi = scaled.normalizedDifference(["B11", "B8"]).rename("NDBI")
    stats = ee.Image.cat(ndvi, mndwi, ndbi).reduceRegion(
        ee.Reducer.mean(), roi, scale=100).getInfo()

    band_expr = ee.Image.cat(ndvi, mndwi, ndbi)

    if quality_tier == "max" and not SANDBOX:
        # ---- 高清模式: 服务端 uint8 分类 (1B/px), 超单请求预算自动分块拼接 ----
        # 六类判定与本地分类完全同序; where 链后写覆盖先写 -> 水体最后写=最优先
        # computePixels 单请求 48MB 硬限: uint8 1B/px -> 单块预算 44M px,
        # 总网格超出即按行切块 (每块独立请求, numpy 纵向拼接)
        # 多期模式单期宽度封顶 2816 (5 期 x 10500 宽整图超内存, 单期才开 10500)
        cls_img = (ee.Image(0).rename("CLS")
                   .where(ndvi.gt(0.2), 1)
                   .where(ndvi.gt(0.45), 2)
                   .where(ndvi.gt(0.6), 3)
                   .where(ndbi.gt(0).And(ndvi.lte(0.45)), 4)
                   .where(mndwi.gt(0.2), 5)
                   .toByte())
        w = 10500 if len(years) == 1 else 2816
        h = max(int(w * span_y / span_x), 1)
        dx = span_x / w
        dy = span_y / h
        # computePixels 单请求 48MB 硬限 + 社区档"用户内存超限": 实测 NPY
        # uint8 按 2B/px 计, 且块越大上游中值合成内存越重 —— 单块 8M px
        # (22M px 实测 EEException User memory limit exceeded)
        rows_cap = max(8_000_000 // w, 1)
        blocks = []
        y0 = 0
        while y0 < h:
            rows = min(rows_cap, h - y0)
            raw = ee.data.computePixels({{
                "expression": cls_img,
                "fileFormat": "NPY",
                "grid": {{
                    "dimensions": {{"width": w, "height": rows}},
                    "crsCode": "EPSG:4326",
                    "affineTransform": {{
                        "translateX": REGION["lon_min"],
                        "translateY": REGION["lat_max"] - y0 * dy,
                        "scaleX": dx, "scaleY": -dy, "shearX": 0, "shearY": 0,
                    }},
                }},
            }})
            blocks.append(np.load(io.BytesIO(raw))["CLS"])
            y0 += rows
        cls = np.vstack(blocks) if len(blocks) > 1 else blocks[0]
    else:
        if SANDBOX:
            w, h = 128, 100
        else:
            # 常规挡预算: 三波段 float32 = 12B/px -> 3.3M px (48MB 硬限内)
            budget_px = 3_300_000
            w = int(min({tier_base}, (budget_px * span_x / span_y) ** 0.5))
            h = max(int(w * span_y / span_x), 1)
        dx = span_x / w
        dy = span_y / h
        raw = ee.data.computePixels({{
            "expression": band_expr,
            "fileFormat": "NPY",
            "grid": {{
                "dimensions": {{"width": w, "height": h}},
                "crsCode": "EPSG:4326",
                "affineTransform": {{
                    "translateX": REGION["lon_min"], "translateY": REGION["lat_max"],
                    "scaleX": dx, "scaleY": -dy, "shearX": 0, "shearY": 0,
                }},
            }},
        }})
        packed = np.load(io.BytesIO(raw))
        if packed.dtype.names:
            nd = packed["NDVI"]
            md = packed["MNDWI"]
            nb = packed["NDBI"]
        else:
            nd, md, nb = packed[..., 0], packed[..., 1], packed[..., 2]
        # 六类确定性分类 (赋值顺序即优先级: 水体最优先, 建筑压过中低植被)
        cls = np.zeros(nd.shape, dtype=np.uint8)   # 0=裸地/其他
        cls[nd > 0.2] = 1                          # 稀疏植被
        cls[nd > 0.45] = 2                         # 中等植被
        cls[nd > 0.6] = 3                          # 茂密植被
        cls[(nb > 0) & (nd <= 0.45)] = 4           # 建筑/不透水面
        cls[md > 0.2] = 5                          # 水体

    panels[year] = cls
    panel_w[year] = w
    panel_h[year] = h
    dist = dict((i, round(float((cls == i).mean()) * 100.0, 2))
                for i in range(6))
    stats["landcover_pct_raw"] = dist
    results[year] = stats

# 中文字体探测: 缺失退化英文, 绝不因字体崩 (与图说横幅同款纪律)
from matplotlib import font_manager as _fm
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
_have = {{f.name for f in _fm.fontManager.ttflist}}
_zh = next((n for n in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                        "Source Han Sans SC", "PingFang SC")
            if n in _have), None)
if _zh:
    matplotlib.rcParams["font.family"] = _zh
matplotlib.rcParams["axes.unicode_minus"] = False

# 莫兰迪低饱和配色 (审美规范): 六类色取自莫兰迪系, 水体/建筑/裸地
# 直接采用用户指定色值; 连续场 diverging 色带见 generator 契约
colors = ["#e2b7ad", "#a9c0a5", "#7d9b76", "#4f6f52", "#92403e", "#2e4a62"]
names = ["裸地/其他", "稀疏植被", "中等植被", "茂密植被", "建筑/不透水面", "水体"] \
    if _zh else ["Bare/Other", "Sparse vegetation", "Moderate vegetation",
                 "Dense vegetation", "Built-up", "Water"]
# 各期 landcover_pct 键从序号映射为类名 (供 METRICS 与图例一致)
for _y in results:
    _raw = results[_y].pop("landcover_pct_raw")
    results[_y]["landcover_pct"] = {{names[i]: _raw.get(i, 0.0) for i in range(6)}}

# ---- 出图: 1 行 n 期对比 (单期即 1x1, 与旧版同构) ----
n = len(years)
_w_sum = sum(panel_w.values())
_h_max = max(panel_h.values())
plt.figure(figsize=(_w_sum / 100, _h_max / 100 + 0.5), dpi=100)
cmap = ListedColormap(colors)
for _i, _y in enumerate(years):
    ax_i = plt.subplot(1, n, _i + 1)
    ax_i.imshow(panels[_y], cmap=cmap, vmin=0, vmax=5, aspect="auto",
                interpolation="nearest")
    ax_i.set_title(_y, fontsize=9)
if n > 1:
    plt.suptitle(years[0] + "-" + years[-1] +
                 (" 土地覆盖变化对比 (Sentinel-2)" if _zh else
                  " Land cover change (Sentinel-2)"), fontsize=10)
else:
    plt.suptitle(years[0] + (" 土地覆盖分类 (Sentinel-2)" if _zh else
                             " Land cover classification (Sentinel-2)"), fontsize=9)

# 图例/指北针/比例尺挂最后一期 (正规分类成果四要素)
ax = plt.subplot(1, n, n)
handles = [Patch(facecolor=c, edgecolor="#8a8a8a", linewidth=0.4, label=nm)
           for c, nm in zip(colors, names)]
plt.legend(handles=handles, loc="lower left", fontsize=7, framealpha=0.85,
           title=("土地覆盖类型" if _zh else "Land cover"), title_fontsize=8)

# 指北针 (右上): N 字 + 竖箭头 (axes 坐标, 与数据范围无关)
ax.annotate("N", xy=(0.955, 0.90), xytext=(0.955, 0.80),
            xycoords="axes fraction", ha="center", va="center",
            fontsize=11, fontweight="bold", color="#2b2b2b",
            arrowprops=dict(arrowstyle="-|>", color="#2b2b2b", lw=1.4))
# 比例尺 (右下): 经度 km 按中心纬度换算, 取图宽 ~22% 的整公里数
_lat0 = (REGION["lat_min"] + REGION["lat_max"]) / 2.0
_km_deg = 111.32 * math.cos(math.radians(_lat0))
_target = span_x * _km_deg * 0.22
_km = min((1, 2, 5, 10, 20, 50, 100, 200), key=lambda v: abs(v - _target))
_seg = _km / _km_deg / span_x
_x0, _y0 = 0.70, 0.05
ax.plot([_x0, _x0 + _seg], [_y0, _y0], color="#2b2b2b", lw=2.2,
        transform=ax.transAxes, solid_capstyle="butt")
for _xx in (_x0, _x0 + _seg):
    ax.plot([_xx, _xx], [_y0 - 0.012, _y0 + 0.012], color="#2b2b2b", lw=1.2,
            transform=ax.transAxes)
ax.text(_x0 + _seg / 2, _y0 + 0.03, str(_km) + " km",
        transform=ax.transAxes, ha="center", va="bottom", fontsize=7.5,
        color="#2b2b2b",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.2))

plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.savefig(OUTPUT_JPEG)

_last = results[years[-1]]
ndvi_mean = float(_last.get("NDVI", 0) or 0)
mndwi_mean = float(_last.get("MNDWI", 0) or 0)
ndbi_mean = float(_last.get("NDBI", 0) or 0)
METRICS.update(ndvi_mean=ndvi_mean, mndwi_mean=mndwi_mean,
               ndbi_mean=ndbi_mean, valid_ratio=1.0)
METRICS["landcover_pct"] = _last.get("landcover_pct")
if len(years) > 1:
    # 多期: 各年指标 + 首末期占比差 (变化检测的核心输出)
    METRICS["years"] = results
    lc0 = results[years[0]].get("landcover_pct") or {{}}
    lc1 = results[years[-1]].get("landcover_pct") or {{}}
    METRICS["landcover_change_pct"] = {{
        k: round(float(lc1.get(k, 0)) - float(lc0.get(k, 0)), 2) for k in lc1}}
'''


@dataclass
class TemplateParams:
    """从任务文本提取的模板参数。"""

    template_id: str
    years: list[str]
    cloud: int
    notes: list[str] = field(default_factory=list)


# 『近N年』支持的期数上限: 每期一次 computePixels + 中值合成, 6 期全量
# 执行 ~1-2 分钟在 240s 超时内; 更多期交给编排层拆分单年任务
_MAX_PERIODS = 6
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _recent_years(task_text: str, today=None) -> list[str] | None:
    """『近N年/最近N年』→ 年份列表; 不含该模式或超上限返回 None。

    未到 9 月时今年夏季窗口不完整, 整体回退一年用最近 N 个完整年份;
    下界夹到 2017 (S2_SR_HARMONIZED 起点)。
    """
    from datetime import date

    m = re.search(r"(?:最近|近)\s*(\d{1,2}|[一两二三四五六七八九十])\s*年", task_text)
    if not m:
        return None
    tok = m.group(1)
    n = _CN_NUM.get(tok) if not tok.isdigit() else int(tok)
    if n is None or n < 2 or n > _MAX_PERIODS:
        return None
    d = today or date.today()
    last = d.year if d.month >= 9 else d.year - 1
    years = [str(y) for y in range(last - n + 1, last + 1) if y >= 2017]
    return years if len(years) >= 2 else None


def extract_params(task_type: str, task_text: str) -> Optional[TemplateParams]:
    """意图+文本 -> 模板参数; 不匹配返回 None。

    匹配条件 (满足其一):
      A) task_type ∈ {land_cover, vegetation, water} 且文本含恰好一个显式年份
         (单期)
      B) task_type ∈ {land_cover, change_detection} 且文本无显式年份但含
         『近N年』(2<=N<=6) → 多期对比模板 landcover_change_v1
      C) task_type ∈ {land_cover, change_detection} 且显式年份 2..6 个、
         首末跨 <=6 年 (clarify 常把『近5年』物化成 "2020-2024" 端点对;
         区间逐期展开) → 多期对比模板。跨度过大 (如 2014-2024 十年) 仍交回
         编排层拆分。
    云量缺省 30%。
    """
    years: list[str] | None = None
    explicit = sorted(set(re.findall(r"20\d{2}", task_text)))
    if task_type in ("land_cover", "vegetation", "water") and len(explicit) == 1:
        years = explicit
    elif task_type in ("land_cover", "change_detection") and not explicit:
        years = _recent_years(task_text)
    elif (task_type in ("land_cover", "change_detection")
          and 2 <= len(explicit) <= _MAX_PERIODS):
        lo, hi = int(explicit[0]), int(explicit[-1])
        if 2 <= hi - lo + 1 <= _MAX_PERIODS:
            years = [str(y) for y in range(lo, hi + 1) if y >= 2017]
            if len(years) < 2:
                years = None
    if years is None:
        return None
    m = re.search(r"云量[^\d%]{0,4}(\d{1,2})\s*%", task_text)
    cloud = int(m.group(1)) if m else 30
    return TemplateParams(
        template_id="land_cover_v1" if len(years) == 1 else "landcover_change_v1",
        years=years, cloud=min(max(cloud, 1), 60))


def render(params: TemplateParams, quality_tier: str = "standard") -> str:
    """渲染完整分析代码 (契约: REGION/SANDBOX/QUALITY_TIER/OUTPUT_JPEG/METRICS)。

    tier_base 数值内联 —— 模板代码在 GEE 受控命名空间执行, 引用不到本模块变量。
    """
    return _LANDCOVER_TEMPLATE.format(
        years_list=", ".join(f'"{y}"' for y in params.years),
        cloud=params.cloud, quality_tier=quality_tier,
        tier_base=_TIER_WIDTH.get(quality_tier, 2048))


def try_template(task_type: str, clarified_task: str, user_input: str,
                 quality_tier: str = "standard") -> tuple[str, TemplateParams] | None:
    """入口: 匹配则返回 (渲染代码, 参数), 否则 None (上层回退 LLM)。"""
    params = extract_params(task_type, f"{clarified_task} {user_input}")
    if params is None:
        return None
    return render(params, quality_tier), params
