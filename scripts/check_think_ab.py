# -*- coding: utf-8 -*-
"""思考链路完整性 A/B: 直连 MiniMax vs 经 /llm/proxy 同题对比。

诊断目标: dsh 里 Think 折叠行内容偏短 —— 是代理转换丢内容, 还是模型本
身思考就短/被请求参数压短。输出两侧 think 正文字数与请求参数观测。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]

key = ""
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("MINIMAX_API_KEY="):
        key = line.split("=", 1)[1].strip()
        break

PROMPT = "分析一下长江三角洲近20年城市扩张对耕地的侵占趋势, 需要哪些遥感数据和方法?请详细论述。"
BODY = {
    "model": "MiniMax-M3",
    "messages": [{"role": "user", "content": PROMPT}],
    "max_tokens": 3000,
    "stream": False,
}

# A) 直连
direct = httpx.post(
    "https://api.minimaxi.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {key}"},
    json=BODY, timeout=300,
).json()
d_content = direct["choices"][0]["message"].get("content") or ""
m = re.search(r"<think>(.*?)</think>", d_content, re.DOTALL)
d_think = (m.group(1) if m else "").strip()
d_body = re.sub(r"<think>.*?</think>", "", d_content, flags=re.DOTALL).strip()
print(f"[直连] think={len(d_think)}字 正文={len(d_body)}字 finish={direct['choices'][0].get('finish_reason')} usage={direct.get('usage', {}).get('completion_tokens')}")

# B) 经代理 (同参)
proxied = httpx.post(
    "http://127.0.0.1:8000/llm/proxy/v1/chat/completions",
    headers={"Authorization": f"Bearer {key}"},
    json=BODY, timeout=300,
).json()
msg = proxied["choices"][0]["message"]
p_think = (msg.get("reasoning_content") or "").strip()
p_body = (msg.get("content") or "").strip()
print(f"[代理] think={len(p_think)}字 正文={len(p_body)}字")
print(f"[无损] think一致={d_think == p_think} 正文一致={d_body == p_body}")
if d_think != p_think:
    print("  直连 think 头80:", d_think[:80].replace(chr(10), " "))
    print("  代理 think 头80:", p_think[:80].replace(chr(10), " "))
