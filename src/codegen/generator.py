"""主 agent 代码生成器: 编排三层防护。

流程:
  1. RAG 检索知识库上下文 (rag_kb)
  2. 主 agent (LLM) 生成遥感处理代码
  3. 第一层 validator 白名单校验 -> 不过则带反馈重生成
  4. 第二层 reviewer 子 agent 审查 -> 不过则带反馈重生成
  5. 第三层 sandbox 沙箱试跑 -> 不过则带反馈重生成
  6. 全过 -> 返回 (code, ready_to_run)

LLM 调用全部做成可注入回调, 测试用 mock, 生产用真实模型。
重试有上限 (防烧 token/配额), 触顶即报错, 不无限循环。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.codegen import failure_store
from src.codegen.domain_validator import verify_domain
from src.codegen.failure_store import make_entry, record_failure
from src.codegen.rag_kb import retrieve_for_task
from src.codegen.reviewer import review_code
from src.codegen.sandbox import sandbox_trial
from src.prompt_fragments import EXPERT_PERSONA, FOUR_RESOLUTIONS, LANDCOVER_SIX_CLASSES
from src.codegen.validator import validate_code

GENERATOR_SYSTEM_PROMPT = (
    EXPERT_PERSONA
    + """你是遥感分析代码生成器。根据用户意图生成遥感处理代码 (Python, 调用 earthengine-api 的 ee 模块)。
铁律 (违反则代码作废):
1. 只用知识库中核实过的数据集 Collection ID 和波段名, 禁止臆造
2. NDVI = (NIR - Red)/(NIR + Red), 分子永远是 NIR-Red
3. 云量字段: Sentinel-2 用 CLOUDY_PIXEL_PERCENTAGE, Landsat 用 CLOUD_COVER, 不可混用
4. Sentinel-2 SR 需乘 0.0001; Landsat C2 L2 需 DN×0.0000275-0.2
5. NDWI(Green,NIR) 和 MNDWI(Green,SWIR) 是两个不同公式
6. 过滤后必须判空: col.size().getInfo()==0 时 raise ValueError("该时段无影像,请放宽时间/云量")
   (实测头号翻车点: 时间窗过窄或云量过低导致空集合, 后续 select 全崩)
7. ImageCollection/Image 不能直接调 normalizedDifference 等 Image 方法 ——
   先 median()/first() 转 Image 再算; 指数用 rename('NDVI') 定名后可
   .select('NDVI/MNDWI') 斜杠组合引用自建波段 (合法惯用法)
8. 多年份任务: for year in years 循环逐年算指标, 结果存 dict 汇总输出;
   SANDBOX=True 时只算第一个年份验证逻辑 (全算必超时)。
   循环体内禁止 try/except 后继续往下用未赋值变量 —— 要么 except 内
   raise, 要么循环前预初始化所有结果变量 (实测 UnboundLocalError 翻车点)

标准工作流 (顺序即规范, 先裁剪研究区再做校正, 违序视为流程错误):
1. 空间裁剪先行: ImageCollection 第一步就 .filterBounds(roi) 收窄研究区
   (城市与区县任务同构, roi 已注入); 需要严格边界时 Image 级再 .clip(roi)
2. 时间裁剪: .filterDate(...) 按任务年份/季节
3. 景级筛云: .filter(ee.Filter.lt(云量字段, 阈值))
4. 逐像元云掩膜 (校正流程第一步): Sentinel-2 用 SCL 剔除 {3 云影, 8/9 云,
   10 卷云}, Landsat 用 QA_PIXEL 云位 —— 掩膜必须在 median 合成之前
   (先掩云后合成, 顺序颠倒会把云边混入中值)
5. 辐射定标 (校正流程第二步): S2 SR ×0.0001 / Landsat DN×0.0000275-0.2,
   在掩膜后的合成影像上做
6. 指数/分类/统计 → 出图 (computePixels 网格按 roi bbox 定义, 输出即裁剪结果)

执行环境契约 (系统已注入, 直接使用, 不要重新初始化):
- ee: 已初始化好的 earthengine 模块 (不要再调 ee.Initialize/Authenticate)
- REGION: dict {lon_min, lat_min, lon_max, lat_max}, 目标分析区域 (城市与区县任务同构, 直接用四值)
- TASK: str, 用户任务原文
- SANDBOX: bool, 沙箱试跑标志 (True 时按模板缩小出图网格快速验证)
- QUALITY_TIER: str, 出图挡位 standard|high|max —— 非 standard 时网格按
  挡位放大 (high≈2048宽/max≈2816宽), 让用户拿到更清晰的成果图

产出契约 (必须, 缺一即失败):
- OUTPUT_JPEG: str — 系统注入的结果图输出路径, 必须 plt.savefig(OUTPUT_JPEG);
  禁止自编输出路径 (示例: 直接写 "/workspace/xx.jpg" 必失败)。
  出图用 matplotlib (Agg 后端): import matplotlib; matplotlib.use("Agg")。
  中文标题缺失字体时用英文, 绝不因字体崩。
  colormap 取法: import matplotlib as mpl 之后用 mpl.colormaps["viridis"]
  (属性访问; 禁止 import matplotlib.colormaps —— 它不是模块, 这样写必崩;
   也禁止已移除的 plt.cm.get_cmap)。
  分类/等级成果图必须正规三要素: 离散色块 (ListedColormap) + 图例
  (matplotlib.patches.Patch 作 legend handles, 列出各类名称) + 标题注数据源;
  分类判定阈值写进代码注释 —— 无图例的分类图视为不合格成果。
  连续量 (指数连续场) 才允许 colorbar。中文字体缺失退化英文, 绝不因字体崩。
  审美规范 (莫兰迪低饱和色系, 禁用高饱和荧光色):
  - 分类离散色: 低饱和莫兰迪色 (水体深蓝 #2e4a62 / 植被灰绿梯度 / 建筑深红棕)
  - 连续场推荐色带 (蓝-白-红 diverging, 用 mpl.colors.LinearSegmentedColormap.
    from_list 构建): ["#2e4a62","#7388A1","#b7c5df","#ffffff","#e2b7ad",
    "#d28e89","#b96a66","#92403e","#701f1f"]
  - 正规成果图还应带比例尺 (按纬度换算 km) 与指北针 (N 箭头, axes fraction
    定位), 参照: ax.annotate("N",...) + ax.plot 比例尺线段
取结果网格的标准模板 (实测可用, 照此写, 勿自创):
w, h = (128, 100) if SANDBOX else (512, 400)
dx = (REGION["lon_max"] - REGION["lon_min"]) / w
dy = (REGION["lat_max"] - REGION["lat_min"]) / h
raw = ee.data.computePixels({
    "expression": ndvi,  # 直接传 ee.Image 对象, 不要 serialize
    "fileFormat": "NPY",
    "grid": {
        "dimensions": {"width": w, "height": h},
        "crsCode": "EPSG:4326",
        "affineTransform": {
            "translateX": REGION["lon_min"], "translateY": REGION["lat_max"],
            "scaleX": dx, "scaleY": -dy, "shearX": 0, "shearY": 0,
        },
    },
})
import io, numpy as np
grid = np.load(io.BytesIO(raw))
grid = grid["NDVI"] if grid.dtype.names else grid  # 结构化数组取波段名
禁止一切地图/缩略图 API: getThumbURL/getMapId/getTileUrl/ee.MapLayer/
ee.data.getMapId 及任何触发 earthengine.maps.* 权限的调用 — 社区档无此权限
(实测 Permission denied)。出图唯一通道 = 上面的 computePixels 模板 + matplotlib。
- METRICS: dict — 中间指标, 至少含: ndvi_mean, nir_mean, red_mean, valid_ratio。
  聚合金样例 (实测可用, 照此写 — rename 定键名, 勿自创):
  roi = ee.Geometry.Rectangle([REGION["lon_min"], REGION["lat_min"],
                               REGION["lon_max"], REGION["lat_max"]])
  def _mean(img, band, name):
      d = img.select([band]).rename([name]).reduceRegion(
          ee.Reducer.mean(), roi, scale=100).getInfo()  # getInfo 取回客户端数值
      return d.get(name) or 0.0  # 空像元/无数据时兜底 0.0, 勿留 None
  ndvi_mean = _mean(ndvi_img, "NDVI", "NDVI")
  nir_mean = _mean(scaled, "B8", "NIR")
  red_mean = _mean(scaled, "B4", "RED")
  METRICS.update(ndvi_mean=ndvi_mean, nir_mean=nir_mean, red_mean=red_mean,
                 valid_ratio=1.0)
  (rename 后键名确定; 不要用 reduceRegion(...).get("B8") 之类未 rename 的键;
   多年份任务把各年指标放 METRICS["years"]={2021:{...},2023:{...}})。

风格: 只用注入的变量, 不读环境变量, 不打印到 stdout, 不联网下载。

只输出代码, 不要解释。
"""
    + "\n\n" + FOUR_RESOLUTIONS
    + "\n\n" + LANDCOVER_SIX_CLASSES
)


# LLM 输出常带 ```python 围栏 (prompt 已禁但模型习惯难改, MiniMax-M3 实测如此)。
# validator 拿到 ``` 开头的"代码"会在 line 1 报语法错误 -> 生成后先提取围栏内容。
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fence(code: str) -> str:
    """提取 ``` 围栏内容; 无围栏则原样 (strip 首尾空白)。"""
    m = _FENCE_RE.match(code.strip())
    return m.group(1) if m else code.strip()


@dataclass
class GenerationResult:
    """代码生成结果。"""

    code: str
    """最终通过三层防护的代码。"""

    ready: bool
    """是否准备好全量执行 (三层全过)。"""

    attempts: int = 0
    """生成尝试次数。"""

    feedback_history: list[str] = field(default_factory=list)
    """每轮被拒原因 (供调试)。"""


def generate_and_validate(
    task: str,
    region: dict | None,
    user_id: str,
    llm_generate: Callable[[str], str],
    llm_review: Optional[Callable[[str], str]] = None,
    max_attempts: int = 3,
    place: str = "",
) -> GenerationResult:
    """生成代码并过三层防护。

    Args:
        task: 用户任务描述 (如 "北京近十年植被覆盖变化")。
        region: {lon_min, lat_min, lon_max, lat_max}; None 为区县 GAUL 模式。
        user_id: 用户标识 (沙箱执行瞬间按它解密凭证, 凭证不进本层)。
        llm_generate: 主 agent 代码生成回调, 输入 prompt 返回代码字符串。
        llm_review: 子 agent 审查回调; None 则走规则兜底。
        max_attempts: 最大重生成次数 (防烧 token)。
        place: 行政区名 (GAUL 模式必给, 注入生成提示)。

    Returns:
        GenerationResult。ready=False 表示达上限仍未通过, 上层应告知用户。
    """
    knowledge_context = retrieve_for_task(_keywords(task))
    feedback_history: list[str] = []
    last_code = ""
    reviewer_rejects = 0  # 审查员连续拒绝计数 (同模型审查倾向找错, 见下)

    for attempt in range(1, max_attempts + 1):
        # 1) 主 agent 生成 (LLM 常无视"只输出代码"习惯性套 ```python 围栏, 提取之)
        prompt = _build_gen_prompt(task, region, knowledge_context, feedback_history, place)
        code = _strip_code_fence(llm_generate(prompt))
        last_code = code

        # 2) 第一层: 白名单校验 (确定性, 抓幻觉)
        v = validate_code(code)
        if not v.passed:
            feedback_history.append(f"[第{attempt}次] 白名单拒绝: {'; '.join(v.issues)}")
            record_failure(make_entry(task, code, "validator", feedback_history[-1]))
            continue

        # 2.5) 学科验证子 agent (G4, 确定性): NDVI 方向/缩放系数/波段对/雷达云掩膜
        #      —— 抓"能跑但学科上算错"的代码, 与白名单(防幻觉)分工不重复
        d = verify_domain(code)
        if not d.passed:
            feedback_history.append(f"[第{attempt}次] 学科验证拒绝: {'; '.join(d.issues)}")
            record_failure(make_entry(task, code, "domain", feedback_history[-1]))
            continue

        # 3) 第二层: 子 agent 审查。
        #    审查分歧降级 (G2 实测): reviewer 与生成器同模型时倾向"总能找到
        #    新毛病", 连拒 2 次后降为顾问 —— 带警告放行进沙箱, 由实测裁决。
        #    确定性层 (validator) 与实测层 (sandbox/anchors) 才有最终裁决权。
        r = review_code(code, task, knowledge_context, llm_review)
        if not r.approved:
            reviewer_rejects += 1
            feedback_history.append(f"[第{attempt}次] 审查意见: {'; '.join(r.comments)}")
            record_failure(make_entry(task, code, "reviewer", feedback_history[-1]))
            if reviewer_rejects < 2:
                continue  # 第一拒绝: 反馈重生成
            feedback_history.append(
                f"[第{attempt}次] 审查分歧降级: 连续 {reviewer_rejects} 次审查拒绝, "
                "意见记为警告, 转沙箱实跑裁决"
            )
            # 落到第 4 层沙箱 (不再 continue)

        # 4) 第三层: 沙箱试跑
        s = sandbox_trial(code, user_id, region)
        if not s.success:
            feedback_history.append(f"[第{attempt}次] 沙箱拒绝: {s.error}")
            record_failure(make_entry(task, code, "sandbox", feedback_history[-1]))
            continue

        # 全过
        return GenerationResult(
            code=code, ready=True, attempts=attempt, feedback_history=feedback_history
        )

    # 达上限未通过
    return GenerationResult(
        code=last_code, ready=False, attempts=max_attempts, feedback_history=feedback_history
    )


def _keywords(task: str) -> list[str]:
    """从任务描述提取关键词供 RAG 检索。简单分词。"""
    return [w for w in task.replace(",", " ").split() if w]


def _build_gen_prompt(
    task: str, region: dict | None, knowledge: str, feedback: list[str],
    place: str = "",
) -> str:
    region_block = (
        f"\n# 区域\n{region}" if region is not None else
        "\n# 区域\n区县模式: REGION 注入为 None, 必须按系统提示的 "
        f"'GAUL 行政区取边界' 段 (geoBoundaries v6), 用 PLACE='{place}' "
        "动态解析 roi 后出图。"
    )
    parts = [
        GENERATOR_SYSTEM_PROMPT,
        f"\n# 用户任务\n{task}",
        region_block,
        f"\n# 知识库\n{knowledge}",
    ]
    if feedback:
        parts.append("\n# 之前的尝试被拒原因 (请修正)\n" + "\n".join(feedback[-2:]))
    # 失败库反例 (P1-2): 检索相似历史失败喂回提示词, 让 LLM 不重蹈覆辙;
    # 检索失败/空库绝不能阻断生成, try/except 兜底 (failure_store 内部
    # 已吞 OSError, 这里防 top_failures 其余异常如脏 jsonl)
    try:
        fails = failure_store.top_failures(task, k=2)
    except Exception:
        fails = []
    if fails:
        lines = "\n".join(f"- {e.reason[:100]}" for e in fails)
        parts.append("\n# 历史失败反例 (禁止重蹈)\n" + lines)
    parts.append("\n请生成遥感处理代码:")
    return "\n".join(parts)
