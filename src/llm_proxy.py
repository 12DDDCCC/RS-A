# -*- coding: utf-8 -*-
"""MiniMax M3 思考通道代理 (UI 优化批次): <think> 文本 -> reasoning_content 字段。

为什么: M3 经 openai-completions 返回的思考是 content 里的 <think>…</think>
纯文本, dsh 把它当正文平铺。pi-ai 明确解析 reasoning_content / reasoning /
reasoning_text 字段为独立思考块 (api/openai-completions.js:353), dsh 前端原生
ReasoningRow ("Think disclosure row") 折叠显示 —— 与 Claude Code / Codex 同形。

挂载: main.py 以 /llm/proxy/v1 前缀挂 router; dsh settings.yaml 的
llm-pi-ai.providers.<x>.baseURL 指向 http://127.0.0.1:8000/llm/proxy/v1 即生效。

鉴权: 透传入站 Authorization; 缺失时回退 env MINIMAX_API_KEY。
流式: SSE 状态机 —— <think>/</think> 可跨 chunk, 尾部疑似前缀暂留缓冲。
"""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

UPSTREAM = os.environ.get("MINIMAX_UPSTREAM", "https://api.minimaxi.com/v1")
router = APIRouter(prefix="/llm/proxy/v1")

OPEN_TAG, CLOSE_TAG = "<think>", "</think>"
# 尾部可能生长为标签的片段集合: <, <t, <th, ... (两种标签并集, 去空串)
_PREFIXES = {OPEN_TAG[:i] for i in range(1, len(OPEN_TAG))} | \
            {CLOSE_TAG[:i] for i in range(1, len(CLOSE_TAG))}


def split_think(text: str, state: str) -> tuple[list[dict], str]:
    """状态机: 一段已知不含"待定前缀尾部"的文本切为增量序列。

    state: "before" | "in" | "after"; 返回 (deltas, 新 state)。
    after 态思考不再出现, 全部按正文 (模型偶发二次标签按字面处理)。
    """
    deltas: list[dict] = []
    pos = 0
    while pos < len(text):
        if state == "before":
            i = text.find(OPEN_TAG, pos)
            if i < 0:
                deltas.append({"kind": "content", "text": text[pos:]})
                break
            if i > pos:
                deltas.append({"kind": "content", "text": text[pos:i]})
            pos, state = i + len(OPEN_TAG), "in"
        elif state == "in":
            i = text.find(CLOSE_TAG, pos)
            if i < 0:
                deltas.append({"kind": "reasoning", "text": text[pos:]})
                break
            if i > pos:
                deltas.append({"kind": "reasoning", "text": text[pos:i]})
            pos, state = i + len(CLOSE_TAG), "after"
        else:
            deltas.append({"kind": "content", "text": text[pos:]})
            break
    return deltas, state


def _tail_partial(text: str) -> str:
    """text 尾部最长且属于 _PREFIXES 的片段 (需留缓冲等下一段拼齐)。"""
    for n in range(min(len(text), len(OPEN_TAG) - 1), 0, -1):
        if text[-n:] in _PREFIXES:
            return text[-n:]
    return ""


class ThinkStreamSplitter:
    """跨 chunk 流式切分: feed(chunk)->deltas, flush()->收尾 deltas。"""

    def __init__(self) -> None:
        self.state = "before"
        self.buf = ""

    def feed(self, chunk: str) -> list[dict]:
        self.buf += chunk
        hold = "" if self.state == "after" else _tail_partial(self.buf)
        body = self.buf[: len(self.buf) - len(hold)] if hold else self.buf
        self.buf = hold
        if not body:
            return []
        deltas, self.state = split_think(body, self.state)
        return deltas

    def flush(self) -> list[dict]:
        if not self.buf:
            return []
        deltas, self.state = split_think(self.buf, self.state)
        self.buf = ""
        return deltas


def _auth_header(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization
    key = os.environ.get("MINIMAX_API_KEY", "")
    return f"Bearer {key}" if key else ""


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    # M3 思考控制翻译 (两态, 小孟决议): dsh 的推理等级 UI 已收敛为 OFF/ON。
    # dsh 实发行为 (观测日志实证): 选 ON(High) 发 reasoning_effort="high";
    # 选 OFF 时 **不发该参数** (OpenAI 风格无 off 档) —— 故缺省必须译为关:
    #   缺省 / off|none|minimal|low|disable|disabled -> disabled (关思考)
    #   其余显式值 (high/medium/default/on...)      -> adaptive  (开=最高)
    # 附带收益: 会话标题等辅助调用本就不带 effort, 自动关思考省配额。
    eff = body.pop("reasoning_effort", None)
    if eff is None or str(eff).lower() in ("off", "none", "minimal", "low",
                                           "disable", "disabled"):
        body["thinking"] = {"type": "disabled"}
    else:
        body["thinking"] = {"type": "adaptive"}
    # 观测旁路: 记录 dsh 实发参数 (思考配额诊断; 不记消息内容)
    try:
        _n_msgs = len(body.get("messages") or [])
        _obs = {"translated_thinking": body["thinking"]["type"],
                "orig_effort": eff, "stream": body.get("stream")}
        from src.runtime.obs import log_event

        log_event("llm_proxy_request", stage="proxy",
                  detail=f"messages={_n_msgs} params={json.dumps(_obs, ensure_ascii=False)}")
    except Exception:
        pass
    headers = {
        "Authorization": _auth_header(request.headers.get("authorization")),
        "Content-Type": "application/json",
    }
    client = httpx.AsyncClient(timeout=600.0)

    if not body.get("stream"):
        upstream = await client.post(f"{UPSTREAM}/chat/completions",
                                     content=json.dumps(body), headers=headers)
        await client.aclose()
        if upstream.status_code != 200:
            return JSONResponse(status_code=upstream.status_code,
                                content=_safe_json(upstream))
        data = upstream.json()
        for choice in data.get("choices", []):
            msg = choice.get("message") or {}
            deltas, _ = split_think(msg.get("content") or "", "before")
            msg["content"] = "".join(d["text"] for d in deltas if d["kind"] == "content")
            reasoning = "".join(d["text"] for d in deltas if d["kind"] == "reasoning")
            if reasoning:
                msg["reasoning_content"] = reasoning
        return JSONResponse(content=data)

    req = client.build_request("POST", f"{UPSTREAM}/chat/completions",
                               content=json.dumps(body), headers=headers)
    upstream = await client.send(req, stream=True)
    if upstream.status_code != 200:
        payload = (await upstream.aread()).decode(errors="replace")[:500]
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(status_code=upstream.status_code,
                            content={"proxy_error": payload})

    splitter = ThinkStreamSplitter()

    async def relay() -> AsyncGenerator[bytes, None]:
        try:
            async for raw in upstream.aiter_lines():
                if not raw.startswith("data:"):
                    continue  # 注释/空行, SSE 客户端不需要
                payload = raw[5:].strip()
                if payload == "[DONE]":
                    for d in splitter.flush():
                        yield _sse({"choices": [{"delta": _delta(d), "index": 0}]})
                    yield b"data: [DONE]\n\n"
                    continue
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                pieces = []
                had_content = False
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    text_piece = delta.pop("content", None)
                    if text_piece is None:
                        continue
                    had_content = True
                    # 首/尾帧可能同时带 role 或 finish_reason: 保留非 content 键
                    if delta or choice.get("finish_reason"):
                        pieces.append(_copy_chunk(chunk, choice, delta))
                    pieces.extend(
                        _copy_chunk(chunk, choice, _delta(d))
                        for d in splitter.feed(text_piece)
                    )
                if pieces:
                    for p in pieces:
                        yield _sse(p)
                elif not had_content:
                    # 纯元数据帧 (role/finish/usage): 原样透传;
                    # content 被全部 hold 的帧不发 (内容仍在缓冲, 下帧带出)
                    yield _sse(chunk)
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


def _delta(d: dict) -> dict:
    return ({"reasoning_content": d["text"]} if d["kind"] == "reasoning"
            else {"content": d["text"]})


def _copy_chunk(chunk: dict, choice: dict, delta: dict) -> dict:
    """以原 chunk 为模板生成单 delta 帧 (保留 model/id 等元数据)。"""
    new_choice = {k: v for k, v in choice.items() if k != "delta"}
    new_choice["delta"] = delta
    out = {k: v for k, v in chunk.items() if k != "choices"}
    out["choices"] = [new_choice]
    return out


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"proxy_error": resp.text[:500]}
