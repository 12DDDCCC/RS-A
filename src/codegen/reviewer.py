"""第二层防护: 子 agent 审查 harness。

子 agent (审查员) 用与主生成器不同的角色/prompt 审查代码, 抓逻辑/语法/方向错误。
对应 grill 决策: 子 agent 换推理链审查, 比主 agent 自查强 (无作者偏见)。

设计为 LLM 无关的接口: review_code 接受一个 llm_review 回调,
实际项目里传入真实 LLM 调用; 测试里传入 mock。

局限 (诚实标注): 子 agent 与主 agent 同根, 对"事实幻觉"仍偏盲,
故必须与第一层 validator (白名单) 叠用, 不可单独依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ReviewReport:
    """子 agent 审查报告。"""

    approved: bool
    comments: list[str] = field(default_factory=list)
    confidence: float = 0.0


# 审查员 prompt 模板 (角色: 挑错的审查员, 不是作者)
REVIEWER_SYSTEM_PROMPT = """你是遥感代码审查员。你的任务是找出主 agent 生成的遥感处理代码中的错误,
重点检查:
1. 波段顺序是否正确 (NDVI 必须 NIR-Red, 不能反)
2. 云量字段是否用对 (S2=CLOUDY_PIXEL_PERCENTAGE, Landsat=CLOUD_COVER)
3. 缩放系数是否应用 (S2×0.0001, Landsat×0.0000275-0.2)
4. 是否会选取错误的数据集或不可用数据
5. 区域/时间过滤是否合理

执行环境契约 (已由系统注入, 引用它们不是"未定义变量", 不要据此拒绝):
- ee: 已初始化的 earthengine 模块 (代码不应再调 ee.Initialize/Authenticate)
- REGION: dict {lon_min, lat_min, lon_max, lat_max} 目标区域
- TASK: str 任务原文
- SANDBOX: bool 沙箱试跑标志 (True 时代码应缩小网格/只算首年快速验证)
- QUALITY_TIER: str 出图挡位 standard|high|max
- PLACE: str 行政区名 (区县 GAUL 模式用它解析 roi, 此时 REGION 可能为 None)
- 代码必须产出: OUTPUT_JPEG (str, 结果 JPEG 路径, 系统已注入不得覆盖) 与
  METRICS (dict, 含 ndvi_mean/nir_mean/red_mean/valid_ratio)
- 出图允许 matplotlib(Agg) + ee.data.computePixels; getThumbURL 无权限属预期

你只审查, 不改写。发现问题就 approved=false 并列出问题。
对不确定的事实 (如某波段名), 宁可报"不确定需核实", 也不要假装确认。

判定纪律 (防宁可错杀): 只拒【确定性错误】—— 语法错、不存在的 API/数据集/
波段名、波段方向写反 (NDVI 分子必须 NIR-Red)、缩放系数导致数量级错误、
明显违背任务目标。以下情形一律 APPROVED 并把意见放 comments:
- 数学上不影响结果的写法差异 (如 normalizedDifference 前的线性缩放)
- 精度/建模偏好 (掩膜 SCL 粒度、云阈值、合成方法) —— 这是领域选择,
  由沙箱试跑与锚点评测实测检验, 不靠审查员主观判断 (四层防护各司其职)
"""


def review_code(
    code: str,
    task: str,
    knowledge_context: str,
    llm_review: Optional[Callable[[str], str]] = None,
) -> ReviewReport:
    """用子 agent 审查代码。

    Args:
        code: 待审代码。
        task: 用户任务描述。
        knowledge_context: RAG 检索到的知识库上下文。
        llm_review: 实际 LLM 调用回调, 输入 prompt 返回审查意见文本。
            为 None 时走规则兜底 (只做基础检查)。

    Returns:
        ReviewReport。
    """
    if llm_review is None:
        return _rule_based_review(code, task, knowledge_context)

    prompt = f"{REVIEWER_SYSTEM_PROMPT}\n\n# 任务\n{task}\n\n# 知识库\n{knowledge_context}\n\n# 待审代码\n```\n{code}\n```\n\n请审查。第一行写 APPROVED 或 REJECTED,后面列问题。"
    raw = llm_review(prompt)
    return _parse_llm_review(raw)


def _rule_based_review(code: str, task: str, knowledge_context: str) -> ReviewReport:
    """无 LLM 时的规则兜底审查 (抓明显方向错误)。"""
    comments: list[str] = []
    low = code.lower()

    # 兜底抓 NDVI 反向 (兜底, 主力是 validator)
    if "ndvi" in low:
        # 形如 (red - nir) 是反向的
        if re_search_reverse_ndvi(code):
            comments.append("疑似 NDVI 反向: Red 在 NIR 之前")
    # 兜底抓云量字段混用
    if "landsat" in low and "cloudy_pixel_percentage" in low:
        comments.append("Landsat 不应用 CLOUDY_PIXEL_PERCENTAGE (那是 S2 字段)")

    if comments:
        return ReviewReport(approved=False, comments=comments, confidence=0.6)
    return ReviewReport(approved=True, comments=["规则兜底审查未发现明显错误"], confidence=0.5)


def re_search_reverse_ndvi(code: str) -> bool:
    """检测 NDVI 公式是否 Red 在前 (反向)。简单启发式。"""
    import re

    # 匹配 (B4 - B8) / (B4 + B8) 或 (red - nir) 这种反向
    rev = re.search(r"\(\s*(?:B4|SR_B4|red)\s*-\s*(?:B8|SR_B5|nir)\s*\)", code, re.IGNORECASE)
    return bool(rev)


def _parse_llm_review(raw: str) -> ReviewReport:
    """解析 LLM 审查输出。"""
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    if not lines:
        return ReviewReport(approved=False, comments=["审查输出为空"], confidence=0.0)
    first = lines[0].upper()
    approved = first.startswith("APPROVED")
    comments = lines[1:] if len(lines) > 1 else ["无具体意见"]
    return ReviewReport(approved=approved, comments=comments, confidence=0.8 if approved else 0.7)
