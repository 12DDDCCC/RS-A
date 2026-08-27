# -*- coding: utf-8 -*-
"""代理端点快速验证: 非流式 <think> 分离效果 (本地诊断用)。"""
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
key = ""
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("MINIMAX_API_KEY="):
        key = line.split("=", 1)[1].strip()
        break

r = httpx.post(
    "http://127.0.0.1:8000/llm/proxy/v1/chat/completions",
    headers={"Authorization": f"Bearer {key}"},
    json={"model": "MiniMax-M3",
          "messages": [{"role": "user", "content": "只回复两个字：就绪"}],
          "max_tokens": 500},
    timeout=120,
)
print("http:", r.status_code)
d = r.json()
m = d["choices"][0]["message"]
print("content:", (m.get("content") or "")[:80].replace("\n", " "))
print("reasoning:", (m.get("reasoning_content") or "")[:80].replace("\n", " "))
