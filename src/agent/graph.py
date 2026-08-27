"""Agent 主状态图 (LangGraph)。

决策5 主链路:
  意图澄清 → (需澄清? 直接END反问) → 生成代码(三层防护) → 云端执行 → 诊断
  诊断 → (异常且未达上限? 回生成纠错) → 输出 JPEG

状态用 TypedDict (LangGraph 惯例), 节点收发 dict。
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    node_clarify,
    node_diagnose,
    node_execute,
    node_generate,
    node_output,
    node_plan,
)


class AgentState(TypedDict, total=False):
    """状态图的状态 schema (LangGraph 用 TypedDict 流转)。"""

    # 输入
    user_input: str
    region: dict | None  # bbox 模式为 dict; 区县 geoBoundaries 模式为 None (代码动态取边界)
    region_source: str  # "bbox" | "district" (区县级默认精度)
    place: str  # 规范地名 (P1-3 caption 首句铁律用; LangGraph 只保留声明过的键)
    user_id: str
    llm_callbacks: dict
    max_retries: int

    # 中间态
    clarified_task: str
    task_type: str
    need_clarify: bool
    clarify_question: str
    analysis_plan: dict  # P1-4: 通过校验的分析计划 (LLM 出, 确定性查)
    generated_code: str
    template_used: str     # O1: 命中的模板 ID (diagnose 据此跳过 LLM 主观判定)
    quality: str           # O1 下载挡位: standard(<1MB) | high(1-10MB) | max(>10MB)
    wiki_hits: list        # W2: 命中的知识库词条 id (检索可观测性; LangGraph 须声明)
    execution_output: str
    execution_metrics: dict
    diagnosis: str
    retry_count: int
    retry_hint: str

    # 输出
    final_output: str
    error: str
    # 错误的三个面向 (P0-3; LangGraph 只保留 schema 声明过的键, 必须在此声明)
    error_code: str
    error_user_message: str
    error_suggestion: str
    # 企业级地基 (E-1)
    session_id: str        # 多轮会话标识 (跨任务记忆)
    task_id: str           # 事件日志归属 (阶段事件写入用)


def _route_after_clarify(state: AgentState) -> str:
    """澄清后: 需反问 -> END(等用户回答); 否则 -> 计划 (P1-4)。"""
    if state.get("error"):
        return "output"
    if state.get("need_clarify"):
        return "output"  # 反问也走 output 节点(把问题作为输出)
    return "plan"


def _route_after_diagnose(state: AgentState) -> str:
    """诊断后: 异常且未达上限 -> 回生成纠错; 否则 -> 输出。

    注意: 路由函数只读 state, 不改 (LangGraph 条件边的写入不生效)。
    retry_count 的递增在 node_generate 里完成 (重试的起点)。
    """
    if state.get("error"):
        return "output"
    diag = state.get("diagnosis", "ok")
    retries = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    if diag in ("bad", "suspicious") and retries < max_retries:
        return "generate"
    return "output"


def build_graph():
    """构建并编译 agent 状态图。"""
    g = StateGraph(AgentState)

    g.add_node("clarify", node_clarify)
    g.add_node("plan", node_plan)
    g.add_node("generate", node_generate)
    g.add_node("execute", node_execute)
    g.add_node("diagnose", node_diagnose)
    g.add_node("output", node_output)

    g.add_edge(START, "clarify")
    g.add_conditional_edges("clarify", _route_after_clarify, {
        "plan": "plan",  # P1-4: 澄清通过后先过计划, 再生成
        "output": "output",
    })
    # 计划出错 (校验不过) 直接走 output 报错; 正常进生成
    g.add_conditional_edges("plan", lambda s: "output" if s.get("error") else "generate", {
        "generate": "generate",
        "output": "output",
    })
    g.add_edge("generate", "execute")
    g.add_edge("execute", "diagnose")
    g.add_conditional_edges("diagnose", _route_after_diagnose, {
        "generate": "generate",
        "output": "output",
    })
    g.add_edge("output", END)

    return g.compile()
