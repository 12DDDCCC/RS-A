"""会话层: 多轮会话记忆 + 事件溯源日志 (企业级地基 E-1)。

差距分析结论: 成熟 harness (dsh/pi) 都立在一根"仅追加、可回放、可派生"
的会话日志上。本模块为我们的版本:

  sessions 表   —— 多轮会话 (用户跨任务延续上下文: "换成上海"可用)
  messages 表   —— 会话内的用户/助手消息 (多轮记忆的载体)
  events 表     —— 任务级事件溯源日志 (阶段进度/用量/审计), 仅追加

事件日志是派生能力的根:
  - SSE 进度 (E-3) 从 events 按 seq 增量推
  - 崩溃恢复: 进程重启后 running 任务合成 interrupted 事件关闭
  - "模型可见即已记录" 的起点: 每次澄清/计划/生成写事件
  - 用量审计: token 计量挂事件 (llm_resilience.UsageMeter 的落点)

设计原则 (借鉴 pi 的 WAL + dsh 的 append-only):
  - 事件只追加不改写; seq 在 (task_id) 内单调递增
  - state 快照 (jobs 表) 与事件日志并存: 快照答"现在什么状态",
    事件答"怎么走到这里的"
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_DIR = paths_cache_root()
DB_PATH = CACHE_DIR / "jobs.db"

_lock = threading.Lock()

# 在 jobs.db 里追加三张表 (与 JobStore 共库, 单进程语义一致)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    summary     TEXT DEFAULT ''          -- 会话摘要 (轮次多了以后压缩用, MVP 留空)
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,           -- user | assistant
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,           -- 事件类型 (stage/usage/audit/...)
    detail      TEXT DEFAULT '',         -- 人话细节 (进度叙事)
    payload     TEXT DEFAULT '{}',       -- 结构化数据 (json)
    ts          TEXT NOT NULL,
    UNIQUE(task_id, seq)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SessionStore:
    """会话/消息/事件 三表仓储 (与 JobStore 共用 sqlite)。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ---------- 会话 ----------

    def create_session(self, user_id: str) -> str:
        session_id = uuid.uuid4().hex[:12]
        with _lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, user_id, _now(), _now()),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def latest_session(self, user_id: str) -> str | None:
        """用户最近会话 (前端"继续上次对话"入口)。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row[0] if row else None

    # ---------- 消息 (多轮记忆) ----------

    def add_message(self, session_id: str, role: str, content: str) -> int:
        assert role in ("user", "assistant")
        with _lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (_now(), session_id),
            )
        return cur.lastrowid

    def history(self, session_id: str, limit: int = 10) -> list[dict]:
        """最近 N 条消息 (时间正序)。多轮上下文注入的数据源。"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------- 事件溯源日志 ----------

    def emit(self, task_id: str, kind: str, detail: str = "",
             payload: dict | None = None) -> int:
        """追加一条事件 (仅追加; seq 由 max+1 分配, 单进程锁保护)。

        kind 约定 (可扩展):
          stage   —— 阶段进度 (detail 是人话, 如 "正在编写分析代码(第2次)")
          usage   —— LLM 用量 (payload: {input_tokens, output_tokens})
          audit   —— 审计事实 (如审批/鉴权事件)
          system  —— 生命周期 (interrupted/recovered 等)
        """
        with _lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            seq = row[0] + 1
            conn.execute(
                "INSERT INTO events (task_id, seq, kind, detail, payload, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, seq, kind, detail,
                 json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
        return seq

    def events_after(self, task_id: str, after_seq: int = 0,
                     limit: int = 200) -> list[dict]:
        """取 seq > after_seq 的事件 (SSE 增量推的数据源)。"""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT seq, kind, detail, payload, ts FROM events "
                "WHERE task_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (task_id, after_seq, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                d["payload"] = {}
            out.append(d)
        return out

    # ---------- 崩溃恢复语义 ----------

    def recover_interrupted(self, running_task_ids: list[str]) -> int:
        """进程重启后: 给遗留 running 任务补 interrupted 系统事件。

        返回处理数。调用方 (main 启动钩子) 负责把 jobs 状态改为 failed。
        """
        n = 0
        for task_id in running_task_ids:
            self.emit(task_id, "system", "进程重启, 任务中断",
                      {"recovered": True})
            n += 1
        return n


# 模块级单例 (与 jobs.store 同库共生命周期)
sessions = SessionStore()


def stage_text(kind: str, detail: str = "") -> dict[str, Any]:
    """构造 stage 事件的便捷参数。"""
    return {"kind": kind, "detail": detail}
