# -*- coding: utf-8 -*-
"""区县任务澄清续跑辅助 (本地诊断用): answer + 轮询终态。"""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

task_id = sys.argv[1]
answer = sys.argv[2] if len(sys.argv) > 2 else (
    "时间范围取2024年7-8月；边界直接用GEE内置的FAO/GAUL/2015/level2按"
    "NAME_2匹配江宁区(Jiangning)，无需外部shp；云量阈值<20%；仅做2024年"
    "单一年份现状分析"
)

import subprocess

token = subprocess.run(
    [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "RS-agent/scripts/decrypt_token.py")],
    capture_output=True, text=True).stdout.strip()
headers = {"Authorization": f"Bearer {token}"}

r = httpx.post(f"http://127.0.0.1:8000/tasks/{task_id}/answer",
               headers=headers, json={"answer": answer}, timeout=15)
print("answer:", r.status_code, r.text[:120])

for i in range(40):
    time.sleep(12)
    s = httpx.get(f"http://127.0.0.1:8000/tasks/{task_id}",
                  headers=headers, timeout=10).json()
    print(f"[{i}] {s['status']}")
    if s["status"] not in ("running", "queued"):
        print(json.dumps(s, ensure_ascii=False, indent=1)[:800])
        if s["status"] == "done":
            img = httpx.get(f"http://127.0.0.1:8000/tasks/{task_id}/result",
                            headers=headers, timeout=60).content
            out = ROOT / "cache" / f"district-{task_id}.jpg"
            out.write_bytes(img)
            print("saved:", out, len(img), "bytes")
        break
