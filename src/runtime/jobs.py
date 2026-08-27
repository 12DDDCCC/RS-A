"""任务生命周期: JobStore(sqlite) + 后台执行 + 澄清续跑。

设计蓝图 P0-4: /analyze 是"同步跑完有状态可循环的分钟级管线"的架构债,
任务必须成为一等公民:
  POST /analyze      -> 202 {task_id} 入队
  GET  /tasks/{id}   -> 状态 + 进度摘要
  GET  /tasks/{id}/result -> JPEG
  POST /tasks/{id}/answer -> 澄清反问的续跑 (决策5 的落地)

要点:
  - sqlite 标准库持久化, 进程重启任务记录不丢
  - state 序列化前剥离 llm_callbacks (callable 不可 json 化) 与
    credentials (P0-5 后 state 只带 user_id, 这里做纵深防御)
  - need_clarify 时挂起任务保留已澄清 state, answer 合入后重新 invoke
  - 产物目录 cache/runs/{task_id}/ 按任务隔离
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.agent.graph import build_graph

CACHE_DIR = paths_cache_root()
RUNS_DIR = CACHE_DIR / "runs"
DB_PATH = CACHE_DIR / "jobs.db"

# 全局并发上限: 防多用户把线程与平台配额同时打爆 (蓝图 P0-4)
MAX_CONCURRENT = 4
_slots = threading.Semaphore(MAX_CONCURRENT)

# 状态机: queued -> running -> (need_clarify -> queued) | done | failed
STATUSES = ("queued", "running", "need_clarify", "done", "failed")

# 序列化时必须剥离的 state 键 (callable / 敏感内容)
_STRIP_KEYS = ("llm_callbacks", "credentials")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    task_id     TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    error_code  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def serialize_state(state: dict) -> str:
    """state -> json 字符串, 剥离不可/不宜序列化的键。"""
    clean = {k: v for k, v in state.items() if k not in _STRIP_KEYS}
    return json.dumps(clean, ensure_ascii=False)


def deserialize_state(state_json: str) -> dict:
    return json.loads(state_json)


class JobStore:
    """sqlite 任务表 (MVP 单进程语义: 不做跨进程锁)。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def create(self, user_id: str, state: dict) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (task_id, user_id, status, state_json, created_at, updated_at) "
                "VALUES (?, ?, 'queued', ?, ?, ?)",
                (task_id, user_id, serialize_state(state), _now(), _now()),
            )
        return task_id

    def get(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM jobs WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def update(self, task_id: str, *, status: str | None = None,
               state: dict | None = None, error_code: str | None = None) -> None:
        sets, params = ["updated_at = ?"], [_now()]
        if status is not None:
            sets.append("status = ?"); params.append(status)
        if state is not None:
            sets.append("state_json = ?"); params.append(serialize_state(state))
        if error_code is not None:
            sets.append("error_code = ?"); params.append(error_code)
        params.append(task_id)
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE task_id = ?", params)

    def has_running(self, user_id: str) -> str | None:
        """per-user 单飞: 同用户已有 running/queued 任务则返回其 task_id。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT task_id FROM jobs WHERE user_id = ? AND status IN ('queued','running') "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row[0] if row else None


# 模块级单例 (main.py 与测试共用)
store = JobStore()


def run_job(task_id: str, callbacks: dict,
            graph_builder: Callable[[], Any] = build_graph) -> None:
    """后台执行一个任务 (阻塞函数, 由线程调用)。

    callbacks 在运行时重新挂回 state (序列化层已剥离)。
    need_clarify -> 挂起保留 state; done/failed -> 终态。
    """
    from src.io import to_jpeg  # 延迟导入避免环

    rec = store.get(task_id)
    if rec is None:
        return
    state = deserialize_state(rec["state_json"])
    state["llm_callbacks"] = callbacks
    state["task_id"] = task_id  # E-1: 各节点写阶段事件的归属

    store.update(task_id, status="running")
    with _slots:  # 并发上限
        try:
            graph = graph_builder()
            final = graph.invoke(state)
        except Exception as e:  # 图内部异常 -> failed, 不裸崩
            # 完整堆栈进观测日志 (G3: 只有单行 str 无法定位偶发故障的真正炸点)
            import traceback as _tb

            stack = _tb.format_exc()
            try:
                from src.runtime.obs import log_event

                log_event("error", task_id=task_id, error_code="INTERNAL",
                          error=str(e), traceback=stack[-4000:])
            except Exception:
                pass
            store.update(task_id, status="failed", error_code="INTERNAL",
                         state={**state, "error": f"{type(e).__name__}: {e}",
                                "error_traceback": stack[-2000:]})
            return

    final = {k: v for k, v in final.items() if k != "llm_callbacks"}

    # 成功分支前置: caption 构造必须在会话落库之前 (G3 审查: 原时序导致
    # 会话落库拿不到 caption, 回退落了服务器内部路径并泄露给 LLM 上下文)
    if not final.get("need_clarify") and not final.get("error"):
        task_dir = RUNS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        from src.io.caption import build_caption  # 延迟导入与 to_jpeg 同模式

        place = final.get("place") or ""
        caption = build_caption(
            task_type=final.get("task_type", ""),
            place=place,
            metrics=final.get("execution_metrics") or {},
        )
        final["caption"] = caption
        try:
            out_path = final.get("final_output")
            # 产物落本任务目录 (cache/runs/{task_id}/), 目录已含 task_id 文件名不再重复
            jpeg = to_jpeg(out_path, task_type=final.get("task_type", ""),
                           out_name="result",
                           caption=caption, place=place, out_dir=task_dir)
            final["result_jpeg"] = jpeg
        except (ValueError, FileNotFoundError) as e:
            final["error"] = f"结果产物无效: {e}"
            final.pop("caption", None)

    # E-1: 助手侧消息落会话 (多轮记忆); 事件日志记录终态
    sid = final.get("session_id") or ""
    try:
        from src.runtime.sessions import sessions as _ss

        if final.get("need_clarify"):
            if sid:
                _ss.add_message(sid, "assistant",
                                f"[需澄清] {final.get('clarify_question','')}")
            _ss.emit(task_id, "stage", "需要补充一点信息", {"status": "need_clarify"})
        elif final.get("error"):
            _ss.emit(task_id, "stage", "本次分析没有成功",
                     {"error_code": final.get("error_code", "INTERNAL")})
            if sid:
                _ss.add_message(sid, "assistant",
                                f"[失败] {final.get('error_user_message', final.get('error',''))}")
        else:
            _ss.emit(task_id, "stage", "分析完成", {"status": "done"})
            if sid and final.get("caption"):
                _ss.add_message(sid, "assistant", str(final["caption"])[:300])
    except Exception:
        pass  # 会话旁路: 不拖垮主链路

    if final.get("need_clarify"):
        # 挂起: 保留已澄清 state 等 /tasks/{id}/answer
        store.update(task_id, status="need_clarify", state=final)
        return

    if final.get("error"):
        store.update(task_id, status="failed",
                     error_code=final.get("error_code", "INTERNAL"), state=final)
        # E 续: 错误落结构化运维日志 (recent_errors 供 /health 自检)
        try:
            from src.runtime.obs import log_event

            log_event("error", task_id=task_id,
                      error_code=final.get("error_code", "INTERNAL"))
        except Exception:
            pass
        return

    store.update(task_id, status="done", state=final)


def submit_answer(task_id: str, answer: str, callbacks: dict,
                  runner: Callable[..., None] = run_job) -> bool:
    """澄清续跑: 把用户回答合入 user_input, 重置澄清标记, 重新入队。"""
    rec = store.get(task_id)
    if rec is None or rec["status"] != "need_clarify":
        return False
    state = deserialize_state(rec["state_json"])
    state["user_input"] = answer
    state["need_clarify"] = False
    state.pop("clarify_question", None)
    # 已澄清的槽位保留在 state 里 (clarified_task/task_type), clarify 节点会续用
    store.update(task_id, status="queued", state=state)
    runner(task_id, callbacks)
    return True
