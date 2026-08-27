# -*- coding: utf-8 -*-
"""llm_proxy 测试: <think> -> reasoning_content 的流式/非流式转换。

状态机是核心资产 (跨 chunk 标签/前缀 hold/after 态), 全部走纯函数级单测;
HTTP 层用 TestClient + monkeypatch 上游验证帧透传与重组。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.llm_proxy import (
    ThinkStreamSplitter,
    split_think,
)


# ---------- 纯状态机 ----------

def test_split_full_block():
    deltas, state = split_think("<think>推理</think>正文", "before")
    assert [d["kind"] for d in deltas] == ["reasoning", "content"]
    assert deltas[0]["text"] == "推理" and deltas[1]["text"] == "正文"
    assert state == "after"


def test_split_no_think():
    deltas, state = split_think("普通回复", "before")
    assert deltas == [{"kind": "content", "text": "普通回复"}]
    assert state == "before"  # 未见过开标签


def test_split_unclosed_think():
    deltas, state = split_think("<think>只开了没关", "before")
    assert deltas == [{"kind": "reasoning", "text": "只开了没关"}]
    assert state == "in"


def test_stream_tag_across_chunks():
    sp = ThinkStreamSplitter()
    a = sp.feed("<thi")
    b = sp.feed("nk>思考中")
    c = sp.feed("</th")
    d = sp.feed("ink>答案")
    assert a == []
    assert b == [{"kind": "reasoning", "text": "思考中"}]
    assert c == []
    assert d == [{"kind": "content", "text": "答案"}]


def test_stream_prefix_in_content_not_tag():
    sp = ThinkStreamSplitter()
    a = sp.feed("3 < 5 但 <t")  # "<" 与 "<t" 都是疑似前缀: 正文先出, 尾部 hold
    b = sp.feed("ag 不是标签")
    assert a == [{"kind": "content", "text": "3 < 5 但 "}]
    assert b == [{"kind": "content", "text": "<tag 不是标签"}]


def test_stream_after_state_no_more_hold():
    sp = ThinkStreamSplitter()
    sp.feed("<think>x</think>")
    out = sp.feed("尾段含 <thi 也直接放行")
    assert out == [{"kind": "content", "text": "尾段含 <thi 也直接放行"}]


def test_stream_flush_drains_buffer():
    sp = ThinkStreamSplitter()
    out = sp.feed("<think>尾段疑似前缀 <thi")  # "<thi" 被 hold
    assert out == [{"kind": "reasoning", "text": "尾段疑似前缀 "}]
    assert sp.flush() == [{"kind": "reasoning", "text": "<thi"}]
    assert sp.flush() == []


# ---------- HTTP 层 ----------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("REMOTE_SENSING_MASTER_KEY", raising=False)
    from src.main import app

    return TestClient(app)


def _fake_upstream(monkeypatch, response_json=None):
    """替掉 httpx.AsyncClient.post: 返回固定非流式响应。"""
    class _Resp:
        status_code = 200

        def json(self):
            return response_json

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def post(self, url, **kw):
            return _Resp()

        async def aclose(self):
            pass

    monkeypatch.setattr("src.llm_proxy.httpx.AsyncClient", _Client)


def test_thinking_translation(client, monkeypatch):
    """reasoning_effort -> M3 thinking 开关翻译 (off 档关闭, 其余全开=最高)。"""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    class _Client:
        def __init__(self, **kw):
            pass

        async def post(self, url, content=None, headers=None):
            captured["body"] = json.loads(content)
            return _Resp()

        async def aclose(self):
            pass

    monkeypatch.setattr("src.llm_proxy.httpx.AsyncClient", _Client)

    r = client.post("/llm/proxy/v1/chat/completions",
                    json={"stream": False, "reasoning_effort": "high",
                          "messages": []})
    assert r.status_code == 200
    assert captured["body"]["thinking"] == {"type": "adaptive"}
    assert "reasoning_effort" not in captured["body"]

    client.post("/llm/proxy/v1/chat/completions",
                json={"stream": False, "reasoning_effort": "low", "messages": []})
    assert captured["body"]["thinking"] == {"type": "disabled"}

    client.post("/llm/proxy/v1/chat/completions", json={"stream": False, "messages": []})
    # 缺省=关: dsh 选 OFF 时 OpenAI 风格无 off 档、不发该参数 (观测实证)
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_nonstream_endpoint_splits_think(client, monkeypatch):
    _fake_upstream(monkeypatch, response_json={
        "choices": [{"message": {"role": "assistant",
                                 "content": "<think>想想</think>好的"}}],
    })
    r = client.post("/llm/proxy/v1/chat/completions", json={"stream": False})
    msg = r.json()["choices"][0]["message"]
    assert msg["content"] == "好的"
    assert msg["reasoning_content"] == "想想"


def test_stream_endpoint_rewrites_deltas(client):
    # 直接驱动 relay: 用真实 SSE 上游桩
    import asyncio

    from src.llm_proxy import ThinkStreamSplitter  # noqa: F401

    lines = [
        'data: {"choices":[{"delta":{"role":"assistant","content":""},"index":0}]}',
        'data: {"choices":[{"delta":{"content":"<think>推理"},"index":0}]}',
        'data: {"choices":[{"delta":{"content":"过程</think>正"},"index":0}]}',
        'data: {"choices":[{"delta":{"content":"文"},"finish_reason":null,"index":0}]}',
        'data: [DONE]',
    ]

    async def fake_aiter(self):
        for ln in lines:
            yield ln

    class _Upstream:
        status_code = 200

        async def aiter_lines(self):
            async for ln in fake_aiter(self):
                yield ln

        async def aclose(self):
            pass

    class _Resp:
        status_code = 200

        async def aread(self):
            return b""

    class _Client:
        def __init__(self, **kw):
            pass

        def build_request(self, *a, **kw):
            return object()

        async def send(self, req, stream=True):
            return _Upstream()

        async def aclose(self):
            pass

    import src.llm_proxy as proxy_mod
    orig_client = proxy_mod.httpx.AsyncClient
    proxy_mod.httpx.AsyncClient = _Client
    try:
        with client.stream("POST", "/llm/proxy/v1/chat/completions",
                           json={"stream": True}) as r:
            body = "\n".join(l for l in r.iter_lines() if l.startswith("data:"))
    finally:
        proxy_mod.httpx.AsyncClient = orig_client

    reasoning = "".join(
        json.loads(seg.split("data:", 1)[1])["choices"][0]["delta"].get("reasoning_content", "")
        for seg in body.split("\n") if seg.startswith("data:") and "[DONE]" not in seg
    )
    content = "".join(
        json.loads(seg.split("data:", 1)[1])["choices"][0]["delta"].get("content", "")
        for seg in body.split("\n") if seg.startswith("data:") and "[DONE]" not in seg
    )
    assert reasoning == "推理过程"
    assert content == "正文"
    assert "data: [DONE]" in body.replace("\r", "")
