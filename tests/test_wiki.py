"""Wiki 知识库测试: frontmatter 解析 / 构建校验 / 三层检索。

临时语料用 monkeypatch 指向 tmp 目录 (REMOTE_SENSING_WIKI_DIR /
REMOTE_SENSING_SOURCES_DIR); 真实语料有专门的守护测试防内容回归。
"""
from __future__ import annotations

import json

import pytest

from src.knowledge import wiki_build as wb
from src.knowledge import wiki_kb

VALID_ENTRY = """---
id: test-idx
domain: analysis
type: indices
title: TESTIDX
aliases:
  - 测试指数
  - Test Index
  - TIDX
themes: [indices, water]
sources:
  - {source_id: src-a, anchor: "[P1]"}
verified: true
related: [test-mate]
---

## 定义
TIDX = (Green − SWIR) / (Green + SWIR)

## 适用条件
- 光学多光谱

## 判读基准
- > 0.2 判为目标 (夏季, Sentinel-2)

## 常见错误
- 与别的指数混用

## 关联词条
[[test-mate]]
"""

MATE_ENTRY = VALID_ENTRY.replace("test-idx", "test-mate").replace(
    "TESTIDX", "TESTMATE").replace("[test-mate]", "[test-idx]").replace(
    "related: [test-mate]", "related: [test-idx]")


def _make_corpus(tmp_path, monkeypatch, entries=None, theme_words=None,
                 sources=None):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    srcs = tmp_path / "sources"
    srcs.mkdir()
    monkeypatch.setenv("REMOTE_SENSING_WIKI_DIR", str(wiki))
    monkeypatch.setenv("REMOTE_SENSING_SOURCES_DIR", str(srcs))

    (wiki / "SPEC.md").write_text(
        "# spec\nWIKI_SPEC_VERSION = 1.1\n", encoding="utf-8")
    registry = {"src-a": {"reliability": "high"},
                "src-low": {"reliability": "low"}}
    registry.update(sources or {})
    (srcs / "_sources.json").write_text(
        json.dumps(registry), encoding="utf-8")
    tw = {"水体": ["test-idx"]}
    tw.update(theme_words or {})
    (wiki / "_theme_words.json").write_text(json.dumps(tw), encoding="utf-8")

    if entries is None:
        entries = {"analysis/indices/test-idx.md": VALID_ENTRY,
                   "analysis/indices/test-mate.md": MATE_ENTRY}
    for rel, text in entries.items():
        p = wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return wiki


# ---------- frontmatter 解析 ----------

def test_parse_frontmatter_subset():
    fm, body = wb.parse_frontmatter(
        "---\nid: x\nthemes: [a, b]\nitems:\n  - {k: v, anchor: \"[P1]\"}\n"
        "flag: true\n---\n\n正文")
    assert fm["id"] == "x"
    assert fm["themes"] == ["a", "b"]
    assert fm["items"] == [{"k": "v", "anchor": "[P1]"}]
    assert fm["flag"] is True
    assert body.startswith("正文")


def test_parse_frontmatter_rejects_missing_close():
    with pytest.raises(wb.WikiValidationError):
        wb.parse_frontmatter("---\nid: x\n正文无闭合")


# ---------- 构建校验 (响亮失败) ----------

def test_build_ok_on_minimal_corpus(tmp_path, monkeypatch):
    _make_corpus(tmp_path, monkeypatch)
    reg = wb.load_sources_registry()
    valid = wb.validate_entries(wb.discover_entries(), reg)
    index = wb.build_index(valid)
    assert set(index["entries"]) == {"test-idx", "test-mate"}
    assert index["theme_words"]["水体"] == ["test-idx"]
    assert index["spec_version"] == "1.1"


@pytest.mark.parametrize("mutate,fragment", [
    # 未注册来源 -> 失败 (无出处不入库)
    ({"sources": '  - {source_id: ghost, anchor: "[P1]"}'}, "未注册"),
    # 缺必填章节 -> 失败
    (None, None),
])
def test_build_failures(tmp_path, monkeypatch, mutate, fragment):
    text = VALID_ENTRY
    if mutate:
        lines = []
        for ln in text.splitlines():
            if ln.startswith("sources:"):
                lines.append("sources:")
                lines.append(mutate["sources"])
            elif ln.strip().startswith("- {source_id"):
                continue
            else:
                lines.append(ln)
        text = "\n".join(lines)
    else:
        text = text.replace("## 常见错误", "## 不存在的章节")
        fragment = "缺必填章节"
    _make_corpus(tmp_path, monkeypatch,
                 entries={"analysis/indices/test-idx.md": text})
    with pytest.raises(wb.WikiValidationError, match=fragment):
        wb.validate_entries(wb.discover_entries(),
                            wb.load_sources_registry())


def test_one_way_related_fails(tmp_path, monkeypatch):
    lone = MATE_ENTRY.replace("related: [test-idx]", "related: []")
    _make_corpus(tmp_path, monkeypatch,
                 entries={"analysis/indices/a.md": VALID_ENTRY,
                          "analysis/indices/b.md": lone})
    with pytest.raises(wb.WikiValidationError, match="单向"):
        wb.validate_entries(wb.discover_entries(), wb.load_sources_registry())


def test_dangling_wikilink_fails(tmp_path, monkeypatch):
    dangling = VALID_ENTRY.replace("[[test-mate]]", "[[ghost-entry]]")
    mate = MATE_ENTRY.replace("related: [test-idx]",
                              "").replace("[[test-idx]]", "")
    _make_corpus(tmp_path, monkeypatch,
                 entries={"analysis/indices/a.md": dangling,
                          "analysis/indices/b.md": mate})
    with pytest.raises(wb.WikiValidationError, match=r"\[\[ghost-entry\]\]"):
        wb.validate_entries(wb.discover_entries(),
                            wb.load_sources_registry())


def test_low_reliability_source_cannot_be_verified(tmp_path, monkeypatch):
    low = VALID_ENTRY.replace("source_id: src-a", "source_id: src-low")
    _make_corpus(tmp_path, monkeypatch,
                 entries={"analysis/indices/test-idx.md": low,
                          "analysis/indices/test-mate.md": MATE_ENTRY})
    with pytest.raises(wb.WikiValidationError, match="low 可信"):
        wb.validate_entries(wb.discover_entries(),
                            wb.load_sources_registry())


def test_theme_words_bad_id_fails_loud(tmp_path, monkeypatch):
    _make_corpus(tmp_path, monkeypatch,
                 theme_words={"植被": ["no-such-id"]})
    reg = wb.load_sources_registry()
    valid = wb.validate_entries(wb.discover_entries(), reg)
    with pytest.raises(wb.WikiValidationError, match="theme_words"):
        wb.build_index(valid)


# ---------- 三层检索 ----------

def _build_and_write(tmp_path) -> None:
    """构建 + 落盘索引 (等价于 main() 的写入步骤)。"""
    valid = wb.validate_entries(wb.discover_entries(),
                                wb.load_sources_registry())
    index = wb.build_index(valid)
    (tmp_path / "wiki" / "_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")


def _built_index(tmp_path, monkeypatch):
    _make_corpus(tmp_path, monkeypatch)
    valid = wb.validate_entries(wb.discover_entries(),
                                wb.load_sources_registry())
    wb.build_index(valid)
    return json.loads((tmp_path / "wiki" / "_index.json").read_text(
        encoding="utf-8"))


def test_search_l2_theme_route(tmp_path, monkeypatch):
    _make_corpus(tmp_path, monkeypatch)
    _build_and_write(tmp_path)
    hits = [e.eid for e in wiki_kb.search_wiki("分析某地水体分布")]
    assert hits == ["test-idx"]


def test_search_no_match_returns_empty(tmp_path, monkeypatch):
    _make_corpus(tmp_path, monkeypatch)
    _build_and_write(tmp_path)
    assert wiki_kb.search_wiki("随便聊聊天气") == []


def test_search_excludes_unverified(tmp_path, monkeypatch):
    draft = VALID_ENTRY.replace("id: test-idx", "id: draft-one").replace(
        "title: TESTIDX", "title: DRAFTONE").replace("verified: true",
                                                     "verified: false") \
        .replace("related: [test-mate]", "related: []") \
        .replace("[[test-mate]]", "") \
        .replace("themes: [indices, water]", "themes: [indices, draft]")
    _make_corpus(tmp_path, monkeypatch, entries={
        "analysis/indices/test-idx.md": VALID_ENTRY,
        "analysis/indices/test-mate.md": MATE_ENTRY,
        "analysis/indices/draft-one.md": draft,
    }, theme_words={"水体": ["test-idx", "draft-one"],
                    "秘密": ["draft-one"]})
    _build_and_write(tmp_path)
    assert [e.eid for e in wiki_kb.search_wiki("水体")] == ["test-idx"]
    # 未核实词条连主题词都召不回
    assert wiki_kb.search_wiki("秘密话题") == []


def _bulk_entry(i: int) -> str:
    """构造自洽的批量词条: 无关联、独立主题标签。"""
    return VALID_ENTRY.replace("id: test-idx", f"id: bulk-{i}") \
        .replace("title: TESTIDX", f"title: BULK{i}") \
        .replace("themes: [indices, water]", f"themes: [indices, w{i}]") \
        .replace("related: [test-mate]", "related: []") \
        .replace("[[test-mate]]", "")


def test_search_max_three_and_prompt_format(tmp_path, monkeypatch):
    corpus = {"analysis/indices/test-idx.md": VALID_ENTRY,
              "analysis/indices/test-mate.md": MATE_ENTRY}
    corpus.update({f"analysis/indices/bulk-{i}.md": _bulk_entry(i)
                   for i in range(5)})
    _make_corpus(tmp_path, monkeypatch, entries=corpus,
                 theme_words={"水体": ["test-idx"] + [f"bulk-{i}" for i in range(5)]})
    _build_and_write(tmp_path)
    hits = wiki_kb.search_wiki("水体")
    assert len(hits) <= wiki_kb.MAX_ENTRIES
    prompt = wiki_kb.format_for_prompt(hits)
    assert "引用库外知识必须声明不确定" in prompt


# ---------- 真实语料守护 (内容回归防线) ----------

def test_real_corpus_builds_with_seed_entries():
    reg = wb.load_sources_registry()
    entries = wb.discover_entries()
    valid = wb.validate_entries(entries, reg)
    # 首批种子 5 指数/3 卫星/2 方法 + W3 扩充 2 物理/2 图像处理 + pitfalls≥1
    assert len(valid) >= 15
    types = {}
    for info in valid.values():
        types[info["type"]] = types.get(info["type"], 0) + 1
    assert types.get("indices", 0) >= 5
    assert types.get("satellites", 0) >= 3
    assert types.get("methods", 0) >= 4
    assert types.get("concept", 0) >= 2
    assert types.get("pitfalls", 0) >= 1
    domains = {info["domain"] for info in valid.values()}
    assert {"physics", "image-processing"} <= domains
    # 种子事实类词条必须全 verified; pitfalls 实测翻车模式允许 verified=false
    # (待第二人对照原文复核, SPEC §7 双检) —— 未核实者不会被检索召回
    seeds = {i: v for i, v in valid.items() if v["type"] != "pitfalls"}
    assert len(seeds) >= 14
    assert all(info["verified"] for info in seeds.values())


# ---------- W2 注入接线 ----------

def test_retrieve_degrades_gracefully_without_index(tmp_path, monkeypatch):
    """索引缺失时 retrieve_for_task 返回空串, 不抛异常拖垮主链。"""
    empty = tmp_path / "empty-wiki"
    empty.mkdir()
    monkeypatch.setenv("REMOTE_SENSING_WIKI_DIR", str(empty))
    assert wiki_kb.retrieve_for_task("分析南京水体") == ""


def test_agent_state_declares_wiki_hits():
    """LangGraph 只保留声明过的键 —— wiki_hits 未声明会被静默丢弃。"""
    from src.agent.graph import AgentState

    assert "wiki_hits" in AgentState.__annotations__


def test_retrieve_degrades_on_corrupted_index(tmp_path, monkeypatch):
    """_index.json 内容损坏 (非法 JSON) 时同样返回空串, 不抛异常。"""
    broken = tmp_path / "broken-wiki"
    broken.mkdir()
    (broken / "_index.json").write_text("{ 这不是合法 JSON ", encoding="utf-8")
    monkeypatch.setenv("REMOTE_SENSING_WIKI_DIR", str(broken))
    assert wiki_kb.retrieve_for_task("分析南京水体") == ""


def test_search_normalizes_fullwidth_and_case(tmp_path, monkeypatch):
    """NFKC + casefold 归一: 全角/大写形态同样命中。"""
    _make_corpus(tmp_path, monkeypatch)
    _build_and_write(tmp_path)
    hits = [e.eid for e in wiki_kb.search_wiki("城区 ＴＩＤＸ 提水")]
    assert "test-idx" in hits
