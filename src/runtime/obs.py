"""结构化日志 + prompt 版本化 (企业级地基 E 续: 运维观测闭环)。

差距清单#11/#14 的最小闭环:
  - prompt 版本常量: 评测/失败库可归因到提示词版本 ("当时模型看到了什么")
  - 结构化 jsonl 日志: cache/logs/YYYY-MM-DD.jsonl, 含 task_id/stage/error_code
    字段; 凭证/令牌绝落日志 (写入前过 _scrub 脱敏)

设计: 观测是旁路 —— 任何日志失败不拖垮主链路 (与 _emit 同哲学)。
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = paths_cache_root() / "logs"

# 模块级 logger (uvicorn 配置下自然汇入标准输出)
logger = logging.getLogger("remote_sensing_agent")


def log_event(kind: str, *, task_id: str = "", stage: str = "",
              error_code: str = "", prompt_version: str = "",
              **extra) -> None:
    """写一条结构化事件日志 (jsonl 落盘 + 标准 logger 双通道)。

    kind: task | stage | error | usage | audit
    失败静默 (观测旁路)。
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "kind": kind,
        "task_id": task_id,
        "stage": stage,
        "error_code": error_code,
        "prompt_version": prompt_version,
    }
    if extra:
        record["extra"] = extra
    line = json.dumps(record, ensure_ascii=False, default=str)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(_scrub(line) + "\n")
    except Exception:
        pass
    try:
        logger.info(_scrub(line))
    except Exception:
        pass


# 绝不落日志的敏感模式 (凭证/令牌/密钥形态)
_SENSITIVE_PATTERNS = [
    re.compile(r'"(pie_token|access_token|api_key|token|password|secret)"\s*:\s*"[^"]*"',
               re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
]


def _scrub(text: str) -> str:
    """敏感值替换为 *** (只动导出副本, 尽力而为)。"""
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub(lambda m: m.group(0).split(":")[0] + ': "***"' if ":" in m.group(0) else "***", text)
    return text


def recent_errors(limit: int = 10, days: int = 7) -> list[dict]:
    """读最近 N 天的错误日志 (/health 自检用)。失败返回空。"""
    out: list[dict] = []
    try:
        files = sorted(LOGS_DIR.glob("*.jsonl"), reverse=True)[:days]
        for path in files:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "error":
                    out.append(rec)
        return out[-limit:]
    except Exception:
        return out
