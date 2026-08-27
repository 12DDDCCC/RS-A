"""结果可读性: 生成图说 (caption) 的纯字符串模板。

P1-3 (设计蓝图铁律): 首句必须说明分析区域——区域框错了,
用户看图说第一句就能发现; 之后按任务类型给"怎么读这张图"的人话说明。

要点:
  - 纯字符串拼装, 不依赖 LLM (不引入新的失败面)
  - place 为空时退化为 "见任务描述", 绝不猜地名 (geo 解析不在此层做)
"""
from __future__ import annotations

# 按任务类型的读图说明 (普通人视角, 遵守术语禁令)
_READ_GUIDES = {
    "vegetation": "绿色越深表示植被越茂盛, 颜色越浅表示植被越稀少。",
    "water": "蓝色越深表示水体, 颜色越浅表示非水体区域。",
    "change_detection": "绿色表示增加, 红色表示减少, 黄色基本没变。",
    "land_cover": "不同颜色代表不同地物类型 (植被/水体/建筑/裸地等)。",
}

_DEFAULT_GUIDE = "颜色深浅代表指标强弱。"


def build_caption(task_type: str, place: str, metrics: dict) -> str:
    """组装图说: 区域铁律句 + 读图说明 + 关键指标 (多行, \\n 分隔)。

    Args:
        task_type: 任务类型 (vegetation/water/change_detection/land_cover/其它)。
        place: 分析区域地名; 空串 -> 首句退化 "见任务描述"。
        metrics: 执行指标; 含数值型 ndvi_mean 时附均值句。

    Returns:
        图说文本 (直接叠加到 JPEG 底部横幅 / 经 /tasks 接口返回)。
    """
    place = (place or "").strip()
    lines = [
        f"分析区域：{place}" if place else "分析区域：见任务描述",
        _READ_GUIDES.get(task_type, _DEFAULT_GUIDE),
    ]

    # 关键指标: isinstance 防脏数据; "区域" 泛化 (分析对象未必是市);
    # 仅植被/时序类任务才提植被指数 (水体任务图说里冒植被均值属语义错位)
    ndvi = (metrics or {}).get("ndvi_mean")
    if isinstance(ndvi, (int, float)) and task_type in ("vegetation", "change_detection"):
        lines.append(f"区域平均植被指数约 {ndvi:.2f}")

    return "\n".join(lines)
