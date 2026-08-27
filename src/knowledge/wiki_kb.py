"""Wiki 知识库检索: 三层路由 (L1 精确 / L2 主题 / L3 包含), 零向量。

SPEC §6 消费方契约 —— 注入 prompt 的唯一入口。
设计哲学与 rag_kb.py 同源: 精确命中人工核实的条目, 不做语义近似召回,
未命中返回空 (不硬凑内容)。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent

MAX_ENTRIES = 3          # 至多 3 条
MAX_CHARS = 800          # 每条正文截断
SCORE_L1, SCORE_L2, SCORE_L3 = 100, 50, 10


@dataclass
class WikiEntry:
    eid: str
    title: str
    file: str
    content: str


def _normalize(s: str) -> str:
    """大小写/全半角归一 (NFKC + casefold), 检索前统一形态。"""
    return unicodedata.normalize("NFKC", s).casefold().strip()


@lru_cache(maxsize=4)
def _load_index(wiki_dir: str, mtime_ns: int) -> dict:
    """mtime 入缓存键 —— 长驻服务进程在索引重建后无需重启即见新库。"""
    idx_path = Path(wiki_dir) / "_index.json"
    return json.loads(idx_path.read_text(encoding="utf-8"))


def _index() -> dict:
    wiki_dir = Path(os.environ.get("REMOTE_SENSING_WIKI_DIR",
                                   str(KNOWLEDGE_DIR / "wiki"))).resolve()
    idx_path = wiki_dir / "_index.json"
    if not idx_path.exists():
        raise FileNotFoundError(
            f"{idx_path} 不存在 —— 先运行 python -m src.knowledge.wiki_build")
    return _load_index(str(wiki_dir), idx_path.stat().st_mtime_ns)


def _vocab(index: dict) -> dict[str, str]:
    """归一化词形 -> 词条 id (id/title/aliases 全量入词表)。"""
    vocab: dict[str, str] = {}
    for eid, info in index["entries"].items():
        for name in [eid, info.get("title") or "", *(info.get("aliases") or [])]:
            n = _normalize(name)
            if n and n not in vocab:
                vocab[n] = eid
    return vocab


def _latin_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text))


def _cjk_ngrams(text: str, max_n: int = 4) -> set[str]:
    grams: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for n in range(2, max_n + 1):
            grams.update(run[i:i + n] for i in range(len(run) - n + 1))
    return grams


def search_wiki(text: str, max_entries: int = MAX_ENTRIES,
                include_unverified: bool = False) -> list[WikiEntry]:
    """三层路由检索任务文本, 返回命中的词条列表。

    L1 精确: 分词命中 id/title/alias (拉丁词元 + 中文 2~4 gram)
    L2 主题: theme_words 中文任务词表路由
    L3 包含: 词形是任务文本子串 (地名式长别名兜底)
    排序: L1 > L2 > L3; 同级按命中数与 related 度; 至多 max_entries 条。
    未核实词条默认不召回 (verified=false 不泄入 prompt, SPEC §7)。
    """
    index = _index()
    entries_meta = index["entries"]
    norm = _normalize(text)
    if not norm or not entries_meta:
        return []

    vocab = _vocab(index)
    scores: dict[str, float] = {}

    def bump(eid: str, score: float):
        scores[eid] = scores.get(eid, 0.0) + score

    # --- L1 精确 ---
    tokens = _latin_tokens(norm) | _cjk_ngrams(norm)
    l1_hits: dict[str, int] = {}
    for tok in tokens:
        eid = vocab.get(tok)
        if eid and len(tok) >= 2:   # 单字符噪声不触发
            bump(eid, SCORE_L1)
            l1_hits[eid] = l1_hits.get(eid, 0) + 1

    # --- L2 主题 ---
    for kw, ids in index.get("theme_words", {}).items():
        if _normalize(kw) in norm:
            for eid in ids:
                bump(eid, SCORE_L2)

    # --- L3 包含 ---
    for name, eid in vocab.items():
        if name in norm:
            bump(eid, SCORE_L3)

    if include_unverified is False:
        scores = {eid: s for eid, s in scores.items()
                  if entries_meta.get(eid, {}).get("verified")}

    # 同分并列时按 "其 related 词条也被命中" 的度数排序 (关联簇整体优先)
    hit_set = set(scores)
    def tiebreak(eid: str) -> tuple:
        meta = entries_meta.get(eid, {})
        rel_names = {meta.get("title", "").lower(), eid}
        rel_names |= {a.lower() for a in meta.get("aliases", [])}
        degree = sum(
            1 for other, ometa in entries_meta.items()
            if other in hit_set and other != eid
            and any(n and n in {ometa.get("title", "").lower(), other,
                                *(a.lower() for a in ometa.get("aliases", []))}
                    for n in rel_names))
        return (-scores[eid], -l1_hits.get(eid, 0), -degree, eid)

    ranked = sorted(scores, key=tiebreak)[:max_entries]

    results: list[WikiEntry] = []
    for eid in ranked:
        meta = entries_meta[eid]
        path = Path(os.environ.get("REMOTE_SENSING_WIKI_DIR",
                                   str(KNOWLEDGE_DIR / "wiki"))) / meta["file"]
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        results.append(WikiEntry(eid=eid, title=meta["title"] or eid,
                                 file=meta["file"], content=content))
    return results


def format_for_prompt(entries: list[WikiEntry]) -> str:
    """把命中词条格式化为 prompt 注入块 (每条截断 MAX_CHARS)。"""
    if not entries:
        return ""
    parts = ["# 遥感知识库 (已核实词条; 引用库外知识必须声明不确定)\n"]
    for e in entries:
        body = e.content.split("---", 2)[-1].strip()   # 剥 frontmatter
        parts.append(f"## {e.title} (wiki/{e.file})\n{body[:MAX_CHARS]}\n")
    return "\n".join(parts)


def retrieve_for_task(task_text: str) -> str:
    """对齐 rag_kb.retrieve_for_task 的消费接口: 文本进, prompt 块出。

    宽松版入口: 索引缺失/目录异常一律返回空串 —— 知识库是生成质量
    的增益而非依赖, 故障不得拖垮主链 (与 obs.py 旁路哲学一致)。
    """
    try:
        return format_for_prompt(search_wiki(task_text))
    except Exception:
        return ""
