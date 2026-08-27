"""Agent 状态图的节点定义 (操作 dict 状态, LangGraph 惯例)。

主链路 (决策5): 意图澄清 → 生成代码(三层防护) → 云端执行 → 看中间结果诊断
                → 异常则回生成器纠错(最多N次) → 输出 JPEG

LLM 调用通过 state["llm_callbacks"] 注入 (测试用 mock, 生产用真实模型)。
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from src.agent.input_guard import sanitize_user_input
from src.codegen.generator import generate_and_validate
from src.platform import get_platform


def _emit(state: dict, kind: str, detail: str = "", payload: dict | None = None) -> None:
    """写阶段事件到事件日志 (E-1)。失败绝不影响主链路 (观测是旁路)。"""
    task_id = state.get("task_id")
    if not task_id:
        return
    try:
        from src.runtime.sessions import sessions

        sessions.emit(task_id, kind, detail, payload)
    except Exception:
        pass  # 观测旁路: 日志写不进去不能拖垮分析
    # E 续: 同步落结构化运维日志 (jsonl)
    try:
        from src.runtime.obs import log_event

        log_event("stage", task_id=task_id, stage=detail)
    except Exception:
        pass


def node_clarify(state: dict) -> dict:
    """节点1: 意图澄清。把模糊输入纠正成明确任务, 必要时反问。"""
    cb: Optional[Callable[[str], str]] = state.get("llm_callbacks", {}).get("clarify")
    if cb is None:
        state.setdefault("clarified_task", state.get("user_input", ""))
        state.setdefault("task_type", "unknown")
        return state

    from src.agent.prompts import CLARIFY_PROMPT

    _emit(state, "stage", "正在理解你的需求…")
    # E-1 多轮记忆: 有会话时注入最近几轮对话, LLM 可解析"换成上海"类指代
    history_block = ""
    sid = state.get("session_id")
    if sid:
        try:
            from src.runtime.sessions import sessions

            hist = sessions.history(sid, limit=6)
            if hist:
                lines = [f"{m['role']}: {m['content'][:80]}" for m in hist]
                history_block = "\n\n# 最近对话 (用户可能指代其中内容, 如'换成上海')\n" + "\n".join(lines)
        except Exception:
            pass
    # T-2: 用户输入过注入防护再拼 prompt (截断/剥围栏/中性化注入指令)
    prompt = f"{CLARIFY_PROMPT}{history_block}\n\n用户输入: {sanitize_user_input(state.get('user_input',''))}\n区域: {state.get('region',{})}"
    raw = cb(prompt)
    try:
        parsed = json.loads(_extract_json(raw))
        state["task_type"] = parsed.get("task_type", "unknown")
        # clarified 来自 LLM 输出, 若 clarify 模型被污染会二次注入下游 prompt, 同样过防护
        state["clarified_task"] = sanitize_user_input(parsed.get("clarified", state.get("user_input", "")))
        state["need_clarify"] = bool(parsed.get("need_clarify", False))
        state["clarify_question"] = parsed.get("clarify_question", "")
    except (json.JSONDecodeError, ValueError):
        state["clarified_task"] = state.get("user_input", "")
    return state


def node_plan(state: dict) -> dict:
    """节点1.5: 生成前计划 (P1-4)。

    LLM 出 JSON 计划 -> validate_plan 确定性三查 (白名单/时间窗/波段)。
    把"合法但错误"的数据集选择从沙箱阶段(贵)前移到零执行成本。
    无 plan 回调时直通 (生成器仍走 RAG, 不阻断主链)。
    """
    cb = state.get("llm_callbacks", {}).get("plan")
    if cb is None:
        return state

    from src.agent.planning import validate_plan
    from src.agent.prompts import PLAN_PROMPT

    # T-2: 任务描述 (LLM 澄清输出或用户原文) 过注入防护再拼 prompt
    task = sanitize_user_input(state.get("clarified_task") or state.get("user_input", ""))
    # 知识块注入: PLAN_PROMPT 的铁律要求"从知识库中选", 必须把白名单真给它
    # (与校验规则共享同一事实源, 否则真实 LLM 只能凭先验盲猜)
    from src.knowledge import load_knowledge

    kb = load_knowledge()
    kb_lines = ["# 数据集白名单 (唯一可选, 形态必须逐字一致)"]
    for entry in kb["datasets"].values():
        if not entry.get("_verified"):
            continue
        cid = entry.get("gee_collection_id", "")
        cov = entry.get("temporal_coverage", {}).get("start", "?")
        # bands/mask_bands 可能是 dict 或 list, 统一取名
        mb = entry.get("mask_bands", {})
        mask_list = list(mb.keys()) if isinstance(mb, dict) else list(mb)
        bands = ",".join(list(entry.get("bands", {}).keys()) + mask_list)
        alt = entry.get("landsat8_collection_id")
        alt_note = f" (备用: {alt}, 自 {entry.get('landsat8_temporal_coverage', {}).get('start', '?')} 起)" if alt else ""
        kb_lines.append(f"- {cid}: 波段[{bands}], 自 {cov} 起{alt_note}")
    prompt = f"{PLAN_PROMPT}\n{chr(10).join(kb_lines)}\n\n任务: {task}\n区域: {state.get('region', {})}"

    issues: list[str] = []
    plan: dict = {}
    for attempt in range(2):  # 一次原问 + 一次带拒绝原因重问
        raw = cb(prompt if not issues else f"{prompt}\n\n# 上次计划被拒原因\n" + "; ".join(issues))
        try:
            parsed = json.loads(_extract_json(raw))
            if not isinstance(parsed, dict):
                raise ValueError("非对象")
            plan = parsed
            report = validate_plan(plan)
            if report.passed:
                state["analysis_plan"] = plan
                return state
            issues = report.issues
        except (json.JSONDecodeError, ValueError):
            issues = ["计划输出不是合法 JSON 对象, 请只输出 JSON"]

    state["error"] = f"分析计划未通过校验: {'; '.join(issues)}"
    return state


def node_generate(state: dict) -> dict:
    """节点2: 生成代码并过三层防护 (调用 codegen)。"""
    # 区县模式 (region=None + region_source=district): 边界解析是确定性工作,
    # 不交给 LLM 写代码 (实测 M3 手写 geoBoundaries 匹配易碎) —— 前置查
    # geoBoundaries 转 bbox, 之后与城市模式完全同构。确定性层才有裁决权。
    if not state.get("region") and state.get("region_source") == "district":
        from src.agent.geo import resolve_district_bbox

        bbox = resolve_district_bbox(state.get("place", ""), state.get("user_id", ""))
        if bbox is None:
            state["error"] = (
                f"没解析到「{state.get('place', '')}」的行政区边界"
                " (重名区县请补充省市, 如 '南京市江宁区'; 名称请核对)"
            )
            state["error_code"] = "DISTRICT_UNRESOLVED"
            return state
        state["region"] = bbox
        _emit(state, "stage", f"已解析行政区边界: {state.get('place', '')}")
    elif not state.get("region"):
        state["error"] = "缺少区域信息, 无法生成代码"
        return state

    # 若是纠错重试 (诊断后折返回来), 递增重试计数。
    # 必须在节点里改 state (条件边函数的写入不生效)。
    # 判据用 diagnosis 而非 retry_hint: 启发式诊断分支不设 hint, LLM 也可能
    # 返回空 hint——若依赖 hint 非空, 会绕过 max_retries 无限循环。
    if state.get("diagnosis") in ("bad", "suspicious"):
        state["retry_count"] = state.get("retry_count", 0) + 1
        _emit(state, "stage", f"结果不理想, 自动纠错重试 (第 {state['retry_count']} 次)")

    # LLM 生成回调是必需的 (不像 clarify/diagnose 有无 LLM 兜底), 缺失时
    # 明确报错而非 KeyError 崩 500。
    llm_generate = state.get("llm_callbacks", {}).get("generate")
    if llm_generate is None:
        state["error"] = (
            "未配置 LLM 回调 (llm_callbacks['generate']), 无法生成代码。"
            "请接入真实模型或注入回调。"
        )
        return state

    _emit(state, "stage", "正在编写分析代码…")

    # 把 retry_hint 拼进任务描述作为修正反馈; 计划块作为生成的宪法前置 (P1-4)
    # T-2: 任务描述过注入防护 (clarified_task 来自 LLM, 防二次注入)
    task = sanitize_user_input(state.get("clarified_task") or state.get("user_input", ""))
    if state.get("analysis_plan"):
        from src.agent.planning import plan_prompt_block

        task = plan_prompt_block(state["analysis_plan"]) + task
    if state.get("retry_hint"):
        task = f"{task}\n[上次结果异常, 修正提示: {state['retry_hint']}]"

    # ---- O1 模板管线: 高频任务走确定性模板 (零 LLM token), 失败回退 LLM ----
    from src.codegen.templates import try_template
    from src.codegen.validator import validate_code
    from src.codegen.domain_validator import verify_domain
    from src.codegen.sandbox import sandbox_trial

    tpl_hit = try_template(state.get("task_type", ""), task,
                           state.get("user_input", ""),
                           state.get("quality", "standard"))
    if tpl_hit is not None and not state.get("retry_hint"):
        code, params = tpl_hit
        _emit(state, "stage",
              f"命中模板管线 ({params.template_id}, {params.year}年) — 免LLM直出")
        v = validate_code(code)
        d = verify_domain(code)
        if v.passed and d.passed:
            s = sandbox_trial(code, state.get("user_id", ""), state["region"])
            if s.success and not s.looks_anomalous():
                state["generated_code"] = code
                state["template_used"] = params.template_id
                return state
            state["retry_hint"] = f"模板试跑未过 ({(s.error or '结果异常')[:80]}), 转自由生成"
        else:
            state["retry_hint"] = "模板代码防护未过 (不应发生, 请检查模板), 转自由生成"
        _emit(state, "stage", "模板未命中, 回退 AI 生成")

    # ---- W2: Wiki 知识库注入 (仅自由生成路径) ----
    # 必须在模板判定之后: 词条含年份数字 (如时间覆盖), 拼早了会破坏
    # try_template 的"恰好一个年份"条件。检索失败静默跳过 (知识库是
    # 增益不是依赖, 故障不拖垮主链)。
    try:
        from src.knowledge.wiki_kb import format_for_prompt, search_wiki

        hits = search_wiki(state.get("clarified_task") or state.get("user_input", ""))
        if hits:
            state["wiki_hits"] = [h.eid for h in hits]
            task = f"{task}\n\n{format_for_prompt(hits)}"
            _emit(state, "stage",
                  "已核对知识库词条: " + "、".join(h.title for h in hits))
    except Exception:
        pass

    result = generate_and_validate(
        task=task,
        region=state["region"],
        user_id=state.get("user_id", ""),
        llm_generate=llm_generate,
        llm_review=state.get("llm_callbacks", {}).get("review"),
        place=state.get("place", ""),
    )
    state["generated_code"] = result.code
    if not result.ready:
        fb = result.feedback_history[-1:] if result.feedback_history else ["未知原因"]
        state["error"] = f"代码生成未通过三层防护 (尝试 {result.attempts} 次): {fb[0]}"
    return state


def node_execute(state: dict) -> dict:
    """节点3: 提交云端执行, 取结果 + 中间指标。

    凭证流 (P0-5): 执行瞬间按 user_id 解密, 传完即弃, 不在 state 里流转。
    """
    if state.get("error"):
        return state

    from src.io.credentials import load_credentials

    try:
        creds = load_credentials(state.get("user_id", ""))
    except FileNotFoundError:
        state["error"] = "未提供凭证且无已存储凭证, 请先绑定平台账号"
        return state
    except RuntimeError as e:
        state["error"] = f"凭证服务不可用: {e}"
        return state

    # 平台可配置 (GEE 已实装为默认; 测试环境由 conftest 强制 pie mock 保基线)
    platform = get_platform(os.environ.get("REMOTE_SENSING_PLATFORM", "gee"))
    _emit(state, "stage", "正在云端计算…")
    result = platform.execute(
        code=state["generated_code"],
        credentials=creds,
        region=state["region"],
        place=state.get("place", ""),  # GAUL 区县模式: 代码按它动态取边界
        quality=state.get("quality", "standard"),
        task=state.get("clarified_task", ""),
    )
    if not result.success:
        state["error"] = f"云端执行失败: {result.error}"
        return state
    state["execution_output"] = result.output_path
    state["execution_metrics"] = result.metrics
    return state


def node_diagnose(state: dict) -> dict:
    """节点4: 看中间结果诊断, 决定是否纠错 (B3 灵魂)。"""
    if state.get("error"):
        state["diagnosis"] = "error"
        return state

    # O1 模板管线: 代码是确定性验证过的模板, 结果由 anchors 客观评测 ——
    # 不走 LLM 主观 diagnose (实测会把 MNDWI 均值负值这类正常形态误判为异常)
    if state.get("template_used"):
        from src.platform.base import ExecutionResult

        er = ExecutionResult(success=True, metrics=state.get("execution_metrics", {}))
        state["diagnosis"] = "bad" if er.looks_anomalous() else "ok"
        _emit(state, "stage", "模板产物锚点评测通过" if state["diagnosis"] == "ok"
              else "模板产物锚点评测异常, 自动纠错")
        return state

    cb: Optional[Callable[[str], str]] = state.get("llm_callbacks", {}).get("diagnose")
    if cb is None:
        # 无 LLM: 用启发式兜底
        from src.platform.base import ExecutionResult

        er = ExecutionResult(success=True, metrics=state.get("execution_metrics", {}))
        state["diagnosis"] = "bad" if er.looks_anomalous() else "ok"
        _emit(state, "stage", "正在检查结果质量…")
        return state

    from src.agent.prompts import DIAGNOSE_PROMPT

    # T-2: 任务描述同样过注入防护 (与 clarify/plan/generate 挂点一致)
    prompt = f"{DIAGNOSE_PROMPT}\n\n任务: {sanitize_user_input(state.get('clarified_task',''))}\n中间指标: {state.get('execution_metrics',{})}"
    raw = cb(prompt)
    try:
        parsed = json.loads(_extract_json(raw))
        state["diagnosis"] = parsed.get("diagnosis", "ok")
        state["retry_hint"] = parsed.get("retry_hint", "")
    except (json.JSONDecodeError, ValueError):
        state["diagnosis"] = "ok"
    return state


def node_output(state: dict) -> dict:
    """终点: 输出 JPEG 路径或反问问题或错误。"""
    # 优先级: 反问 > 错误 > 结果
    if state.get("need_clarify"):
        state["final_output"] = f"[需澄清] {state.get('clarify_question','')}"
        return state
    if state.get("error"):
        from src.agent.errors import classify_error

        retries_exhausted = (
            state.get("diagnosis") in ("bad", "suspicious")
            and state.get("retry_count", 0) >= state.get("max_retries", 2)
        )
        ue = classify_error(state["error"], retries_exhausted=retries_exhausted)
        state["error_code"] = ue.code
        state["error_user_message"] = ue.user_message
        state["error_suggestion"] = ue.suggestion
        state["final_output"] = f"[失败] {ue.user_message}"
        return state
    state["final_output"] = state.get("execution_output")
    return state


def _extract_json(raw: str) -> str:
    """从 LLM 输出里提取 JSON 块 (兼容 ```json 包裹)。"""
    raw = raw.strip()
    if "```" in raw:
        parts = raw.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                return p
    return raw
