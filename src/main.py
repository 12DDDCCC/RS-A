"""FastAPI 入口: 异步任务制 (设计蓝图 P0-4/P0-5 + 企业级地基 E-1/E-3)。

端点:
  POST /analyze                  - 提交分析任务 -> 202 {task_id} (后台执行, 可带 session_id 续会话)
  GET  /tasks/{id}               - 任务状态 + 进度人话 + 澄清问题/错误信息
  GET  /tasks/{id}/events        - SSE 流式进度 (阶段事件增量推送, E-3)
  GET  /tasks/{id}/result        - 取结果 JPEG (done 状态)
  POST /tasks/{id}/answer        - 回答澄清反问, 续跑任务 (决策5 落地)
  POST /credentials              - 存储用户凭证 (加密)
  GET  /sessions/latest          - 用户最近会话 (前端"继续上次对话")
  POST /domain/verify            - 学科6规则校验 (dsh/MCP 工具层的只读镜像, B2-fast)
  GET  /prompts/domain           - PV2.0 专家人设三段下发 (systemPrompt 注入事实源, B3)
  GET  /health                   - 健康检查 + 降级状态自检

安全:
  - 凭证 per-user 加密存储, state 只带 user_id, 执行瞬间解密用完即弃
  - 凭证绝不落日志; 访问令牌鉴权 + 任务归属校验
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# .env 只放本机敏感配置 (master key / LLM key), 已被 .gitignore 排除。
# override=False: 已存在的环境变量优先 (测试 monkeypatch / 显式 export 不被覆盖)。
# REMOTE_SENSING_NO_DOTENV=1: 测试环境彻底禁读 (conftest 设置; 否则测试内
# 延迟 import main 时 load_dotenv 会绕过 fixture 的环境清理注入本机真 key)。
if os.environ.get("REMOTE_SENSING_NO_DOTENV") != "1":
    load_dotenv()

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.agent.errors import classify_error
from src.agent.geo import guess_district, resolve_place
from src.io import has_credentials, store_credentials
from src.io.auth import (
    extract_bearer,
    has_access_token,
    issue_access_token,
    verify_access_token,
)
from src.runtime.jobs import run_job, store, submit_answer
from src.runtime.sessions import sessions
from src.llm_proxy import router as llm_proxy_router  # M3 <think>->reasoning_content 代理 (UI批次)

# 关键: 凭证绝不落日志
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="AI 遥感 Agent", version="0.3.2")
app.include_router(llm_proxy_router)

# dsh web (:3080) 的 RS-A 凭证面板跨源直连本后端 (仅本机回环源, 凭证操作仍需 Bearer)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3080", "http://localhost:3080"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------- 启动钩子: 崩溃恢复语义 (E-1) ----------

@app.on_event("startup")
def _recover_interrupted_tasks() -> None:
    """进程重启后: 遗留 running 任务补 interrupted 事件并转 failed。

    对齐成熟 harness 的崩溃恢复语义: 不留僵尸 running。
    """
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT task_id FROM jobs WHERE status IN ('queued','running')"
        ).fetchall()
    for (task_id,) in rows:
        sessions.emit(task_id, "system", "服务重启, 任务中断", {"recovered": True})
        store.update(task_id, status="failed", error_code="INTERRUPTED",
                     state={"error": "服务重启导致任务中断, 请重新发起",
                            "user_id": ""})


# ---------- 请求模型 ----------

class Region(BaseModel):
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float


class AnalyzeRequest(BaseModel):
    user_input: str = Field(..., description="中文自然语言意图, 如'北京近十年植被覆盖变化'")
    user_id: str = Field(..., description="用户唯一标识 (凭证按它加密存取)")
    quality: str = Field(default="standard", description="出图挡位: standard(<1MB)|high(1-10MB)|max(>10MB)")
    # 普通人说城市名, 不说经纬度; 两者都给则以 region 为准
    place: Optional[str] = Field(default=None, description="地名, 如'北京'/'成都市' (自动转区域)")
    region: Optional[Region] = Field(default=None, description="经纬度框 (开发者用; 与 place 至少其一)")
    session_id: Optional[str] = Field(default=None, description="会话 ID (续会话/多轮记忆; 省略则新开")


class LLMConfigRequest(BaseModel):
    provider: str = Field("minimax", description="minimax | deepseek | zhipu | qwen")
    api_key: str = Field(..., description="对应供应商的 API Key")


class ThinkingRequest(BaseModel):
    user_id: str
    mode: str = Field(..., description="思考挡位: OFF | ON (大小写不敏感)")


class AnswerRequest(BaseModel):
    answer: str = Field(..., description="对澄清反问的回答")


class DomainVerifyRequest(BaseModel):
    code: str = Field(..., description="待检的 GEE 代码 (JavaScript)")


class CredentialRequest(BaseModel):
    user_id: str
    credentials: dict


# ---------- LLM 回调工厂 ----------

def _make_callbacks(task_id: str | None = None) -> dict:
    """构造 agent 所需的 LLM 回调 (F2: 接真实 LLM)。

    task_id 给定时接线 on_usage -> events 表 usage 事件 (评测/成本核算数据源,
    G2 补): 每次真实 LLM 调用后记录 {callback, input_tokens, output_tokens, calls}。
    探测不到任何供应商 key -> LLMNotConfiguredError -> 返回空 dict:
    clarify/diagnose 节点走兜底, generate 节点报"未配置 LLM 回调"
    (零 key 现状完全不变, 不瞎调假 API)。
    """
    try:
        from src.agent.llm_client import LLMNotConfiguredError, real_callbacks

        if not task_id:
            return real_callbacks()

        def _on_usage(callback_name: str, usage: dict) -> None:
            # 观测旁路语义: 记账失败绝不拖垮主链
            try:
                sessions.emit(task_id, "usage", f"LLM {callback_name}", {
                    "callback": callback_name, **usage,
                })
            except Exception:
                pass

        return real_callbacks(on_usage=_on_usage)
    except LLMNotConfiguredError:
        return {}


# ---------- 轻量限流 (G3 安全补强: 防刷/防配额耗尽) ----------

import time as _time
from collections import defaultdict, deque
from threading import Lock

_RATE_LIMIT = 6          # 每 user 每分钟最多提交次数 (MVP 经验值)
_RATE_WINDOW_S = 60.0
_submit_history: dict[str, deque] = defaultdict(deque)
_rl_lock = Lock()


def _check_rate_limit(user_id: str) -> None:
    """滑动窗口限流; 超限抛 429。进程内实现, 多副本部署需换集中式 (后期)。"""
    now = _time.monotonic()
    with _rl_lock:
        q = _submit_history[user_id]
        while q and now - q[0] > _RATE_WINDOW_S:
            q.popleft()
        if len(q) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail={
                "code": "RATE_LIMITED",
                "message": "提交太频繁啦, 请稍等一分钟再试",
                "suggestion": f"每分钟最多 {_RATE_LIMIT} 次分析",
            })
        q.append(now)


# ---------- 进度人话 (状态 -> 用户可读) ----------

_PHASE_TEXT = {
    "queued": "已收到, 排队中",
    "running": "正在分析中",
    "need_clarify": "需要补充一点信息",
    "done": "分析完成",
    "failed": "本次分析没有成功",
}


# ---------- 鉴权 (蓝图盲区#1 最小修复) ----------

def _require_auth(user_id: str, authorization: str) -> None:
    """校验 Bearer 令牌与 user_id 匹配, 不过 -> 401 (人话)。"""
    token = extract_bearer(authorization)
    if not verify_access_token(user_id, token):
        raise HTTPException(status_code=401, detail={
            "code": "UNAUTHORIZED",
            "message": "访问令牌缺失或不正确",
            "suggestion": "请使用绑定账号时返回的访问令牌 (Authorization: Bearer ...)",
        })


# ---------- 端点 ----------

@app.post("/llm-config")
def llm_config(req: LLMConfigRequest):
    """桌面版向导: 运行时配置 LLM Key (立即生效 + 持久化 rs-a.env)。

    免鉴权说明: 本端点仅面向单机桌面形态 (服务只绑 127.0.0.1, 无凭证前的
    鸡蛋问题); 云端部署形态必须删掉此端点 (见 obsidian 37)。
    """
    import re as _re

    if req.provider not in ("minimax", "deepseek", "zhipu", "qwen"):
        raise HTTPException(status_code=400, detail={
            "code": "BAD_PROVIDER",
            "message": f"不支持的供应商: {req.provider}",
            "suggestion": "可选 minimax / deepseek / zhipu / qwen",
        })
    if not req.api_key or len(req.api_key) < 8:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_KEY", "message": "API Key 不像合法值 (过短)",
            "suggestion": "请粘贴完整的 API Key",
        })
    # 格式预检 (用户模拟测试发现的易忽视点): bad-key-123 这类明显无效值
    # 照单全收会让用户跑完任务才在云端 401。各家 Key 形态差异大
    # (MiniMax 实测 sk-cp- 开头, DeepSeek sk- 开头), 只做保守长度拦截
    if len(req.api_key) < 16:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_KEY",
            "message": "这个 Key 太短, 不像完整复制",
            "suggestion": "请回到供应商控制台复制完整的 API Key",
        })
    os.environ[req.provider.upper() + "_API_KEY"] = req.api_key
    os.environ["REMOTE_SENSING_LLM_PROVIDER"] = req.provider  # 显式选择即覆盖
    # 持久化: launcher 下次启动加载 (cwd 已被锚定到 exe/仓库根)
    try:
        env_file = Path.cwd() / "rs-a.env"
        lines = []
        if env_file.exists():
            lines = [l for l in env_file.read_text(encoding="utf-8").splitlines()
                     if not _re.match(rf"^{req.provider.upper()}_API_KEY=", l)
                     and not l.startswith("REMOTE_SENSING_LLM_PROVIDER=")]
        lines.append(f"{req.provider.upper()}_API_KEY={req.api_key}")
        lines.append(f"REMOTE_SENSING_LLM_PROVIDER={req.provider}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        persisted = True
    except OSError:
        persisted = False   # 只生效本次运行, 不阻塞
    return {"configured": True, "provider": req.provider,
            "persisted": persisted}


def _platform_name() -> str:
    """health 展示的平台名 (与 nodes.py 的选择逻辑同源)。"""
    name = os.environ.get("REMOTE_SENSING_PLATFORM", "gee")
    return "gee (real)" if name == "gee" else "pie-engine (mock)"


@app.get("/health")
def health():
    """健康检查 + 降级自检 (占位项显性化) + 运维摘要 (E 续)。"""
    from src.runtime.obs import recent_errors

    recent = recent_errors(limit=5)
    try:
        from src.codegen.failure_store import new_error_patterns

        contract_gaps = new_error_patterns()
    except Exception:
        contract_gaps = []
    return {
        "status": "ok",
        "version": app.version,
        "contract_gaps": contract_gaps,
        "master_key": "ok" if os.environ.get("REMOTE_SENSING_MASTER_KEY") else "missing",
        "llm": "configured" if _llm_ready() else "missing",
        "platform": _platform_name(),
        "observability": {
            "recent_errors": len(recent),
            "latest_error_code": recent[-1]["error_code"] if recent else "",
        },
    }


def _llm_ready() -> bool:
    """探测 LLM 是否已配置 (不真正建客户端)。"""
    try:
        from src.agent.llm_client import detect_provider

        return detect_provider() is not None
    except Exception:
        return False


@app.post("/credentials")
def save_credentials(req: CredentialRequest):
    """首次绑定凭证 -> 签发访问令牌 (仅此一次展示)。

    已绑定 -> 409: 无令牌不可覆盖 (防他人用 user_id 抢绑/覆盖凭证)。
    已知限制: 首次绑定可被抢注, 完整解法需带外注册, 属后期。
    """
    if has_credentials(req.user_id) or has_access_token(req.user_id):
        raise HTTPException(status_code=409, detail={
            "code": "ALREADY_BOUND",
            "message": "该账号已绑定过平台凭证",
            "suggestion": "如需更换凭证, 请先删除当前绑定后重新操作",
        })
    store_credentials(req.user_id, req.credentials)
    token = issue_access_token(req.user_id)
    # 不返回路径细节 (防信息泄露); 令牌仅此一次返回
    return {"stored": True, "access_token": token}


@app.get("/credentials/status")
def credentials_status(user_id: str, authorization: str = Header(default="")):
    """当前绑定状态 (仅回显账号邮箱, 凭证本体永不外泄)。"""
    _require_auth(user_id, authorization)
    if not has_credentials(user_id):
        return {"bound": False, "email": ""}
    from src.io.credentials import load_credentials

    try:
        email = (load_credentials(user_id) or {}).get("service_account_email", "")
    except Exception:
        email = ""
    return {"bound": True, "email": email}


@app.post("/credentials/replace")
def replace_credentials(req: CredentialRequest, authorization: str = Header(default="")):
    """更换 GEE 凭证 (RS-A 前端面板用)。

    已绑定时必须持有效 Bearer 才能换 (防抢注覆盖 —— 与首次绑定的 409
    语义同一防线); 仅覆盖凭证本体, 不轮换访问令牌 —— dsh 侧工具链
    (env 注入的 Bearer) 换绑后零中断。
    """
    if has_credentials(req.user_id):
        _require_auth(req.user_id, authorization)
    store_credentials(req.user_id, req.credentials)
    return {"replaced": True, "token_rotated": False}


@app.post("/users/local")
def ensure_local_user(req: CredentialRequest, authorization: str = Header(default="")):
    """本地多账号 (S1): 确保 user_id 存在 (凭证+令牌), 返回其访问令牌。

    单机形态的账号语义: 本机回环内自服务建号 —— 与 /rs-auth-token 同一
    信任边界 (仅本机进程可达); 云端 C 端上线后由云账号 JWT 取代。
    已存在时须持有效 Bearer 才能取回令牌 (防抢注, 同 /replace 防线);
    不存在则按传入凭证建档并首签令牌。
    """
    from src.io.auth import get_access_token, has_access_token, issue_access_token

    if has_credentials(req.user_id) or has_access_token(req.user_id):
        _require_auth(req.user_id, authorization)
        # 取回现有令牌而非重签 —— 重签会使 dsh 侧已注入的 Bearer 失效
        existing = get_access_token(req.user_id) or issue_access_token(req.user_id)
        return {"created": False, "access_token": existing}
    if not req.credentials:
        raise HTTPException(status_code=400, detail={
            "code": "NO_CREDS",
            "message": "新用户需要同时提供平台凭证",
            "suggestion": "请粘贴 GEE Service Account JSON",
        })
    store_credentials(req.user_id, req.credentials)
    return {"created": True, "access_token": issue_access_token(req.user_id)}


@app.get("/thinking")
def get_thinking():
    """当前后端分析管线思考挡位 (OFF/ON)。"""
    from src.agent.llm_client import get_thinking_mode

    return get_thinking_mode()


@app.post("/thinking")
def set_thinking(req: ThinkingRequest, authorization: str = Header(default="")):
    """运行时切换后端分析管线思考挡位 (免重启, 立即对后续任务生效)。

    OFF: 正文独占 8k 预算秒级响应 (默认); ON: 开思考+24k 预算 (1-3 分钟级,
    疑难任务用)。切换为进程级全局态, 影响所有后续任务。
    """
    _require_auth(req.user_id, authorization)
    from src.agent.llm_client import set_thinking_mode

    try:
        return set_thinking_mode(req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={
            "code": "BAD_THINKING_MODE",
            "message": str(e),
            "suggestion": "mode 仅支持 OFF / ON",
        })


@app.post("/analyze", status_code=202)
def analyze(req: AnalyzeRequest, background: BackgroundTasks,
            authorization: str = Header(default="")):
    """提交分析任务, 立即返回 task_id (异步执行, 决策5 的架构前提)。"""
    _require_auth(req.user_id, authorization)
    _check_rate_limit(req.user_id)  # G3: 防刷/防配额耗尽
    # 1) 区域: region 优先 > 地名解析 (城市命中后区县提级) > 区县 geoBoundaries 兜底 > 400
    # place 取解析出的规范地名 (P1-3 caption 首句铁律; region 直传时无地名)
    # 区县级默认精度: 原文含更细的 区/县/旗 名时提级为区县模式 (边界由
    # 生成代码从 WM/geoLab/geoBoundaries/600/ADM2 动态解析), 城市级 bbox 仍为无区县时的默认。
    place = ""
    region_source = "bbox"
    if req.region is not None:
        region = req.region.model_dump()
    else:
        source_text = req.place or req.user_input
        pr = resolve_place(source_text) if source_text else None
        if pr is not None and pr.confidence == "ambiguous":
            raise HTTPException(status_code=400, detail={
                "code": "AMBIGUOUS_PLACE",
                "message": f"「{source_text}」有多个地方",
                "candidates": pr.ambiguous_cities,
                "suggestion": f"请说明是哪一个: {' / '.join(pr.ambiguous_cities)}",
            })
        if pr is not None:  # high: 城市命中
            region, place = pr.bbox, pr.name
            district = guess_district(source_text)
            if district:  # 城市语境下的区县提级 ("南京市江宁区" / "上海浦东新区")
                region, place, region_source = None, district, "district"
        else:
            district = guess_district(source_text)
            if district:
                region = None
                place = district
                region_source = "district"
            else:
                raise HTTPException(status_code=400, detail={
                    "code": "NO_REGION",
                    "message": "还不知道要分析哪个地方",
                    "suggestion": "请告诉我一个城市名, 比如: 上海",
                })

    # 2) 凭证必须已存 (state 不再带凭证, P0-5)
    if not has_credentials(req.user_id):
        raise HTTPException(status_code=400, detail={
            "code": "NO_CREDS",
            "message": "还没有绑定卫星数据平台的账号",
            "suggestion": "请先绑定你的卫星数据平台账号 (前端右上角「绑定账号」)",
        })

    # 3) per-user 单飞: 同用户已有在跑任务 -> 409
    running = store.has_running(req.user_id)
    if running:
        raise HTTPException(status_code=409, detail={
            "code": "ALREADY_RUNNING",
            "message": "你已有一个分析正在进行",
            "task_id": running,
        })

    # 4) 会话: 带合法 session_id 则续会话 (多轮记忆), 否则新开 (E-1)
    session_id = req.session_id or ""
    if session_id:
        rec = sessions.get_session(session_id)
        if rec is None or rec["user_id"] != req.user_id:
            session_id = ""  # 无效/非本人的会话静默降级为新会话
    if not session_id:
        session_id = sessions.create_session(req.user_id)
    sessions.add_message(session_id, "user", req.user_input)

    # 5) 入队, 后台执行 (BackgroundTasks 在响应后跑; TestClient 下同步完成)
    task_id = store.create(req.user_id, {
        "user_input": req.user_input,
        "region": region,
        "place": place,  # caption 首句铁律: 让用户第一眼就能发现区域框错
        "region_source": region_source,  # bbox=本地bbox | district=geoBoundaries区县动态取边界
        "quality": req.quality if req.quality in ("standard", "high", "max") else "standard",
        "user_id": req.user_id,
        "session_id": session_id,  # clarify 节点注入多轮历史
        "task_id": None,  # run_job 会回填 (事件日志归属)
        "retry_count": 0,
        "max_retries": 2,
    })
    sessions.emit(task_id, "stage", "已收到, 排队中")
    background.add_task(run_job, task_id, _make_callbacks(task_id))
    return {"task_id": task_id, "status": "queued", "session_id": session_id}


@app.get("/tasks/{task_id}")
def get_task(task_id: str, authorization: str = Header(default="")):
    """任务状态 + 进度/澄清问题/错误 (用户面人话, 开发者面 tech_detail)。"""
    rec = store.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_auth(rec["user_id"], authorization)  # 归属校验: 令牌须属任务主人

    out = {
        "task_id": task_id,
        "status": rec["status"],
        "phase_text": _PHASE_TEXT.get(rec["status"], ""),
    }
    state = json.loads(rec["state_json"])

    if rec["status"] == "need_clarify":
        out["question"] = state.get("clarify_question", "")
    if rec["status"] == "failed":
        ue = classify_error(state.get("error", ""))
        # O5 token 专项: 技术细节截断 200 字 (编排层重试时省上下文)
        out["error"] = {
            "code": ue.code,
            "message": ue.user_message,
            "suggestion": ue.suggestion,
            "tech_detail": ue.tech_detail[:200],
        }
    if rec["status"] == "done":
        out["result_url"] = f"/tasks/{task_id}/result"
        out["caption"] = state.get("caption", "")  # P1-3 图说 (区域 + 怎么读这张图)
    return out


@app.get("/tasks/{task_id}/result")
def get_result(task_id: str, authorization: str = Header(default="")):
    """取结果 JPEG (仅 done 状态)。"""
    rec = store.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_auth(rec["user_id"], authorization)
    if rec["status"] != "done":
        raise HTTPException(status_code=409, detail=f"任务未完成 (当前: {rec['status']})")

    state = json.loads(rec["state_json"])
    jpeg = state.get("result_jpeg", "")
    if not jpeg:
        raise HTTPException(status_code=500, detail="任务完成但无产物")
    return FileResponse(jpeg, media_type="image/jpeg", filename="result.jpg")


@app.post("/tasks/{task_id}/answer", status_code=202)
def answer(task_id: str, req: AnswerRequest, background: BackgroundTasks,
           authorization: str = Header(default="")):
    """回答澄清反问 -> 合入已澄清上下文续跑 (不再从头澄清)。"""
    rec = store.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_auth(rec["user_id"], authorization)
    _check_rate_limit(rec["user_id"])  # G3 审查: answer 触发全管线重跑, 与 analyze 同限流
    ok = submit_answer(task_id, req.answer, _make_callbacks(task_id), runner=lambda tid, cb: background.add_task(run_job, tid, cb))
    if not ok:
        rec = store.get(task_id)
        status = rec["status"] if rec else "不存在"
        raise HTTPException(status_code=409, detail=f"任务当前状态不支持回答 (当前: {status})")
    return {"task_id": task_id, "status": "queued"}


# ---------- SSE 流式进度 (E-3) ----------

@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str, after: int = 0, authorization: str = Header(default="")):
    """任务阶段事件 SSE 流 (text/event-stream)。

    事件数据 = 事件日志按 seq 增量推送 (kind/detail/ts);
    任务到终态(done/failed/need_clarify)后发 event: close 并断流。
    ?after=N 可断线续传 (从 seq>N 继续)。
    """
    rec = store.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _require_auth(rec["user_id"], authorization)

    async def stream():
        last_seq = after
        idle_rounds = 0
        while True:
            events = sessions.events_after(task_id, last_seq)
            for ev in events:
                last_seq = ev["seq"]
                data = json.dumps({
                    "seq": ev["seq"], "kind": ev["kind"],
                    "detail": ev["detail"], "payload": ev["payload"],
                    "ts": ev["ts"],
                }, ensure_ascii=False)
                yield f"event: progress\ndata: {data}\n\n"
                # 终态事件 -> 收尾断流
                if ev["payload"].get("status") in ("done", "failed", "need_clarify"):
                    yield "event: close\ndata: {}\n\n"
                    return
            # 任务已达终态但没等到带 status 的事件 (历史遗留) -> 直接收尾
            cur = store.get(task_id)
            if cur and cur["status"] in ("done", "failed", "need_clarify") and not events:
                idle_rounds += 1
                if idle_rounds >= 2:
                    yield "event: close\ndata: {}\n\n"
                    return
            else:
                idle_rounds = 0
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/sessions/latest")
def latest_session_of_user(user_id: str, authorization: str = Header(default="")):
    """用户最近会话 + 最近消息 (前端"继续上次对话"入口)。"""
    _require_auth(user_id, authorization)
    sid = sessions.latest_session(user_id)
    if sid is None:
        return {"session_id": None, "messages": []}
    return {"session_id": sid, "messages": sessions.history(sid, limit=10)}


# ---------- 领域能力下发 (B2-fast: dsh 工具层 / MCP 的数据源) ----------
# 鉴权说明: 与 /health 同级的基础设施端点 —— 不触碰用户数据与凭证,
# 内容全部来自本仓库文件 (学科规则/PV prompt), 故不强制 Bearer。

@app.post("/domain/verify")
def domain_verify(req: DomainVerifyRequest):
    """遥感学科6规则校验 (C05 domain_validator 的 HTTP 只读镜像, 零副作用)。

    dsh 主模型在提交分析前自查用 (事前保险); 后端管线内的
    domain_validator 校验 (事中) 仍是权威, 两者不互替。
    """
    from src.codegen.domain_validator import verify_domain

    rep = verify_domain(req.code)
    return {"passed": rep.passed, "issues": rep.issues}


@app.get("/prompts/domain")
def domain_prompts():
    """PV2.0 专家人设三段下发 (B3 systemPrompt 注入的单一事实源)。

    prompt 升级只改后端并递增 PROMPT_VERSION; dsh 插件每次启动拉取,
    自身不留编辑入口。
    """
    from src.agent.prompts import PROMPT_VERSION
    from src.prompt_fragments import (
        EXPERT_PERSONA,
        FOUR_RESOLUTIONS,
        LANDCOVER_SIX_CLASSES,
    )

    return {
        "prompt_version": PROMPT_VERSION,
        "sections": {
            "expert_persona": EXPERT_PERSONA,
            "four_resolutions": FOUR_RESOLUTIONS,
            "landcover_six_classes": LANDCOVER_SIX_CLASSES,
        },
    }


@app.get("/knowledge/catalog")
def knowledge_catalog():
    """数据集知识库摘要 (C21 的 HTTP 只读镜像, 四分辨率选图依据)。

    供 dsh/MCP 工具层 get_dataset_catalog 消费; 波段表保留颜色与分辨率,
    指数公式原样下发 —— LLM 不许凭记忆报波段/ID, 一律以此为准。
    """
    from pathlib import Path

    kb_path = Path(__file__).resolve().parent / "knowledge" / "datasets.json"
    raw = json.loads(kb_path.read_text(encoding="utf-8"))

    datasets = []
    for key, ds in raw.get("datasets", {}).items():
        datasets.append({
            "key": key,
            "full_name": ds.get("name", ""),
            "gee_collection_id": ds.get("gee_collection_id", ""),
            "purpose": ds.get("purpose", ""),
            "resolution_m": ds.get("spatial_resolution_m"),
            "revisit_days": ds.get("revisit_days"),
            "temporal_coverage": ds.get("temporal_coverage"),
            "bands": {
                band: (meta if isinstance(meta, str)
                       else meta.get("color") or meta.get("description", ""))
                for band, meta in ds.get("bands", {}).items()
            },
        })
    indices = {k: v for k, v in raw.get("indices_reference", {}).items()
               if not k.startswith("_")}
    return {"datasets": datasets, "indices": indices}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
