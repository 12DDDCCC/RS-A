# -*- coding: utf-8 -*-
"""遥感 Agent 的 MCP (Model Context Protocol) stdio server。

把 Agent 核心能力暴露为标准 MCP 工具, 供 Claude Desktop / dsh
(dsh-mcp-client) 等 MCP 客户端以外部工具形式调用。

协议: JSON-RPC 2.0 over stdio (每行一个请求), 仅实现最小必要方法:
initialize / notifications/initialized / tools/list / tools/call / ping。
纯标准库实现 —— 不引入 mcp SDK 依赖 (项目克制原则)。

工具 (B2-fast 起五件套):
  analyze_area       异步提交, 返回 task_id (兼容旧客户端)
  execute_and_wait   阻塞式: 提交+轮询至终态 (上限10分钟); need_clarify
                     快速返回反问文本 —— dsh 编排首选, 免多轮轮询烧 token
  verify_domain_code 学科6规则自检 (NDVI方向/S2缩放/Landsat缩放/
                     MNDWI波段对/NDBI方向/S1无云), 提交分析前自查
  get_task_status    状态兜底查询
  get_result_image   取结果 JPEG 落盘

启动配置 (env):
  REMOTE_SENSING_API_BASE  后端地址 (默认 http://127.0.0.1:8000)
  REMOTE_SENSING_TOKEN     访问令牌 (Bearer; 由 /credentials 绑定后获得)
  REMOTE_SENSING_USER      用户标识 (默认 mcp-user)

dsh 接入示例 (cordis.yml patch, 见 RS-agent/dsh/cordis.patch.yml):
  - insert:
      - id: mcp-remote-sensing
        name: '@deepseek-ai/dsh-mcp-client'
        config:
          serverName: rs
          transport: stdio
          command: D:/rs-agent-workspace/.venv/Scripts/python.exe
          args: [D:/rs-agent-workspace/src/mcp_server.py]
          toolCallTimeoutMs: 700000   # execute_and_wait 最长等 10 分钟
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

API_BASE = os.environ.get("REMOTE_SENSING_API_BASE", "http://127.0.0.1:8000")
TOKEN = os.environ.get("REMOTE_SENSING_TOKEN", "")
USER = os.environ.get("REMOTE_SENSING_USER", "mcp-user")
POLL_INTERVAL_S = 3          # execute_and_wait 轮询间隔
WAIT_TIMEOUT_S = 600         # 阻塞上限 10 分钟 (蓝图 B2 规格)

TOOLS = [
    {
        "name": "analyze_area",
        "description": (
            "提交中文遥感分析任务 (异步)。支持植被监测/水体提取/土地覆盖分类/"
            "时序变化检测等。返回 task_id, 用 get_task_status 轮询进度。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_input": {"type": "string", "description": "中文自然语言需求, 如 '分析北京2024年6月植被'"},
                "place": {"type": "string", "description": "城市名 (如 北京/南京), 省略则从 user_input 解析"},
            },
            "required": ["user_input"],
        },
    },
    {
        "name": "verify_domain_code",
        "description": (
            "遥感学科规则自检 (6条: NDVI方向/S2缩放0.0001/Landsat缩放/"
            "MNDWI用Green+SWIR/NDBI方向/S1不做云掩膜)。"
            "生成 GEE 代码后、提交分析前调用, 返回 issues 列表或 PASS。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "待检的 GEE 代码"}},
            "required": ["code"],
        },
    },
    {
        "name": "execute_and_wait",
        "description": (
            "阻塞式提交中文遥感分析任务并等待终态 (上限10分钟)。"
            "返回 need_clarify 时把 question 转问用户, 带原 task_id 与用户回答重调;"
            "返回 done 时含图说与 task_id (再调 get_result_image 取图)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_input": {"type": "string", "description": "中文自然语言需求"},
                "place": {"type": "string", "description": "城市名 (如 北京/南京)"},
                "task_id": {"type": "string", "description": "澄清续跑时传上一轮返回的原任务 ID"},
                "clarifications": {"type": "string", "description": "对反问的回答 (续跑时必填)"},
                "timeout_s": {"type": "integer", "description": "等待上限秒数, 默认600, 最大900"},
            },
            "required": ["user_input"],
        },
    },
    {
        "name": "get_dataset_catalog",
        "description": (
            "查询卫星数据集知识库摘要 (波段/时间覆盖/分辨率/指数公式)。"
            "规划分析前先查此目录选数据, 禁止凭记忆报数据集 ID 或波段名。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_task_status",
        "description": "查询遥感分析任务的当前状态 (running/need_clarify/done/failed)、人话进度与错误信息。",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "get_result_image",
        "description": "取已完成任务的结果 JPEG 并保存到本地路径, 返回保存位置与文件大小。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "save_path": {"type": "string", "description": "结果图保存的完整路径"},
            },
            "required": ["task_id", "save_path"],
        },
    },
]


def _api(method: str, path: str, body: dict | None = None, raw: bool = False):
    r = urllib.request.Request(
        f"{API_BASE}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            data = resp.read()
            return json.loads(data.decode()) if not raw else data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"后端 {e.code}: {e.read().decode()[:200]}")


def _execute_and_wait(args: dict) -> str:
    """阻塞语义 (蓝图 B2 核心): 提交/续跑 -> 轮询 -> 终态文本。"""
    task_id = args.get("task_id", "")
    if task_id:
        answer = args.get("clarifications", "").strip()
        if not answer:
            return "澄清续跑需要 clarifications (用户对反问的回答)。"
        _api("POST", f"/tasks/{task_id}/answer", {"answer": answer})
    else:
        d = _api("POST", "/analyze", {
            "user_input": args["user_input"], "user_id": USER,
            "place": args.get("place"),
        })
        task_id = d["task_id"]

    deadline = time.time() + min(int(args.get("timeout_s", WAIT_TIMEOUT_S)), 900)
    while True:
        s = _api("GET", f"/tasks/{task_id}")
        status = s["status"]
        if status == "need_clarify":
            return (f"需要澄清: {s.get('question', '')}\n"
                    f"(把用户回答放入 clarifications、并带 task_id={task_id} 重调 execute_and_wait)")
        if status == "done":
            return (f"分析完成。task_id={task_id}\n图说: {s.get('caption', '')}\n"
                    f"请调 get_result_image(task_id='{task_id}', save_path=工作区内路径) 取图。")
        if status == "failed":
            err = s.get("error") or {}
            msg = err.get("message") or s.get("phase_text", "")
            sug = err.get("suggestion", "")
            return f"分析失败: {msg}" + (f" 建议: {sug}" if sug else "")
        if time.time() >= deadline:
            return (f"等待超时, 任务仍在后台执行。task_id={task_id}, "
                    f"稍后可用 get_task_status 查询。")
        time.sleep(POLL_INTERVAL_S)


def call_tool(name: str, args: dict) -> str:
    if name == "get_dataset_catalog":
        cat = _api("GET", "/knowledge/catalog")
        lines = []
        for ds in cat["datasets"]:
            cov = ds.get("temporal_coverage") or "见知识库"
            res = f"{ds['resolution_m']}m" if ds.get("resolution_m") else "?"
            lines.append(f"- {ds['key']} ({ds['gee_collection_id']}) {res} {cov}: {ds['purpose']}")
        idx = ", ".join(cat.get("indices", {}).keys())
        return "数据集目录 (选数据以此为准, 禁止凭记忆):\n" + "\n".join(lines) + f"\n指数: {idx}"
    if name == "analyze_area":
        d = _api("POST", "/analyze", {
            "user_input": args["user_input"], "user_id": USER,
            "place": args.get("place"),
        })
        return (f"已受理 (202)。task_id={d['task_id']}, session={d.get('session_id')}. "
                f"用 get_task_status 查询进度。")
    if name == "verify_domain_code":
        rep = _api("POST", "/domain/verify", {"code": args["code"]})
        if rep["passed"]:
            return "学科校验 PASS (6规则全过)"
        return "学科校验发现问题:\n- " + "\n- ".join(rep["issues"])
    if name == "execute_and_wait":
        return _execute_and_wait(args)
    if name == "get_task_status":
        s = _api("GET", f"/tasks/{args['task_id']}")
        parts = [f"status={s['status']}", s.get("phase_text", "")]
        if s.get("question"):
            parts.append(f"反问: {s['question']}")
        if s.get("error"):
            parts.append(f"错误: {s['error']['message']}")
        if s.get("caption"):
            parts.append(f"图说: {s['caption']}")
        return "\n".join(p for p in parts if p)
    if name == "get_result_image":
        data = _api("GET", f"/tasks/{args['task_id']}/result", raw=True)
        path = args["save_path"]
        with open(path, "wb") as f:
            f.write(data)
        return f"结果图已保存: {path} ({len(data)} bytes)"
    raise ValueError(f"未知工具: {name}")


def handle(req: dict) -> dict | None:
    """处理一条 JSON-RPC 请求, 返回响应 (通知类返回 None)。"""
    method = req.get("method", "")
    rid = req.get("id")

    def result(res):
        return {"jsonrpc": "2.0", "id": rid, "result": res}

    def error(code: int, msg: str):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return result({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "remote-sensing-agent", "version": "0.4.0"},
        })
    if method.startswith("notifications/"):
        return None  # 通知无需回应
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        params = req.get("params", {})
        try:
            text = call_tool(params["name"], params.get("arguments", {}))
            return result({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return result({"content": [{"type": "text", "text": f"执行失败: {e}"}],
                           "isError": True})
    return error(-32601, f"method 不可用: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                continue  # 合法 JSON 但非对象 (数字/数组/null) —— 无法关联 id, 静默跳过
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(req)
        except Exception as e:  # 兜底: 单请求异常不崩 stdio 服务
            resp = {"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None,
                    "error": {"code": -32603, "message": f"内部错误: {e}"}}
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
