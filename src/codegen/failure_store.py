"""失败案例库 (P1-2): 三层防护的每次拒绝都落盘, 供检索复用与回归分析。

设计:
  - 追加写 cache/failures/YYYY-MM.jsonl (按月分文件, 便于归档)
  - taxonomy 规则映射 (MVP 不上 LLM 分类, 确定性优先)
  - top_failures 用字符重叠检索, 复用 rag_kb 的简单匹配风格
  - record_failure 内部吞 OSError: 失败库是旁路观测, 绝不允许它
    弄崩生成主管线 (埋点不改行为的硬保证)

测试通过 monkeypatch 模块级 _FAILURES_DIR 隔离到 tmp_path。
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
import re
from pathlib import Path

# 失败库根目录 (默认 cache/failures/, 与 io/output.py 的 CACHE_DIR 同法求得);
# 模块级变量, 测试 monkeypatch 它实现目录隔离
_FAILURES_DIR = paths_cache_root() / "failures"

# 拒绝层标识 (对应三层防护)
LAYER_VALIDATOR = "validator"
LAYER_REVIEWER = "reviewer"
LAYER_SANDBOX = "sandbox"

# taxonomy 失败分类
TAXONOMY_BAD_DATASET = "BAD_DATASET"
TAXONOMY_BAD_BAND = "BAD_BAND"
TAXONOMY_BAD_FORMULA = "BAD_FORMULA"
TAXONOMY_SYNTAX_ERROR = "SYNTAX_ERROR"
TAXONOMY_REVIEW_REJECTED = "REVIEW_REJECTED"
TAXONOMY_SANDBOX_REJECTED = "SANDBOX_REJECTED"


@dataclass
class FailureEntry:
    """一条失败记录 (三层防护某层拒绝了一次代码)。"""

    ts: str
    """ISO 时间戳。"""

    task: str
    """用户任务描述。"""

    code: str
    """被拒的代码。"""

    reject_layer: str
    """拒绝层: validator / reviewer / sandbox。"""

    reason: str
    """拒绝原因 (即该轮 feedback 文本)。"""

    taxonomy: str
    """失败分类 (见 classify)。"""


def classify(reject_layer: str, reason: str) -> str:
    """taxonomy 规则映射 (MVP: 按拒绝层 + reason 关键词)。

    validator 层按关键词: 含"数据集"->BAD_DATASET, 含"波段"->BAD_BAND,
    含"语法"->SYNTAX_ERROR, 其余 BAD_FORMULA; reviewer 层一律 REVIEW_REJECTED;
    sandbox 层一律 SANDBOX_REJECTED。
    """
    if reject_layer == LAYER_VALIDATOR:
        if "数据集" in reason:
            return TAXONOMY_BAD_DATASET
        if "波段" in reason:
            return TAXONOMY_BAD_BAND
        if "语法" in reason:
            return TAXONOMY_SYNTAX_ERROR
        return TAXONOMY_BAD_FORMULA
    if reject_layer == LAYER_REVIEWER:
        return TAXONOMY_REVIEW_REJECTED
    return TAXONOMY_SANDBOX_REJECTED


def make_entry(task: str, code: str, reject_layer: str, reason: str) -> FailureEntry:
    """构造 FailureEntry (自动填时间戳与 taxonomy), 不落盘。"""
    return FailureEntry(
        ts=datetime.now().isoformat(timespec="seconds"),
        task=task,
        code=code,
        reject_layer=reject_layer,
        reason=reason,
        taxonomy=classify(reject_layer, reason),
    )


def record_failure(entry: FailureEntry) -> FailureEntry:
    """把失败记录追加写入 cache/failures/YYYY-MM.jsonl。

    埋点安全阀: 落盘异常只报 stderr 不上抛——失败库是旁路观测,
    绝不允许它改变 generator 主管线行为。
    """
    try:
        path = _FAILURES_DIR / f"{datetime.now():%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[failure_store] 落盘失败(忽略, 不影响主管线): {e}", file=sys.stderr)
    return entry


def load_failures() -> list[FailureEntry]:
    """读回全部失败记录 (按月份文件名序合并); 目录不存在返回空。"""
    entries: list[FailureEntry] = []
    if not _FAILURES_DIR.exists():
        return entries
    for p in sorted(_FAILURES_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(FailureEntry(**json.loads(line)))
    return entries


def top_failures(task: str, k: int = 2) -> list[FailureEntry]:
    """按与 task 的字符重叠数检索最相关的 k 条失败 (rag_kb 式简单匹配)。

    只返回有重叠(>0)的记录; 零重叠视为不相关。重叠越多排越前。
    用途: 下轮生成前把相似失败喂回提示词, 让 LLM 不重蹈覆辙。
    """
    def overlap(other: str) -> int:
        return len(set(task) & set(other))

    hits = [(overlap(e.task), e) for e in load_failures()]
    hits = [(s, e) for s, e in hits if s > 0]
    hits.sort(key=lambda t: -t[0])
    return [e for _, e in hits[:k]]

def new_error_patterns(recent: int = 20, baseline: int = 60) -> list[str]:
    """O3 契约缺口自动检查: 最近 recent 条失败里, 错误签名是否为近
    baseline 条中未见过的模式 —— 新签名即 prompt 契约缺口的信号。

    签名 = reason 的首段 (冒号/括号前主干), 剥离具体变量值。
    供 /health 与未来仪表盘消费; 任何异常静默返回空 (观测旁路语义)。
    """
    try:
        entries = load_failures()
        if len(entries) <= recent:
            return []
        def sig(e: FailureEntry) -> str:
            head = re.split(r"[:：(（]", e.reason or "", maxsplit=1)[0].strip()
            return re.sub(r"[0-9]+", "N", head)[:40]
        base = {sig(e) for e in entries[-baseline:-recent]}
        out: list[str] = []
        for e in entries[-recent:]:
            s = sig(e)
            if s and s not in base and s not in out:
                out.append(s)
        return out[:5]
    except Exception:
        return []
