#!/usr/bin/env python3
"""Wiki 新知识源注册脚手架 (SPEC §8)。

生成 sources 元数据骨架并登记进 _sources.json, 之后人工往生成的
摘录文件里补 [锚点] 知识点片段 —— 逆向禁止 (先词条后出处) 在此被
流程性阻断: 词条构建器会校验 source_id 必须已在本注册表。

用法:
  python wiki_add_source.py --source-id book-meianxin-daolun --type book \
      --title "遥感导论" --authors "梅安新;彭望琭;秦其明;刘慧平" \
      --year 2001 --venue "高等教育出版社" --reliability medium
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "knowledge"
SOURCES_DIR = KNOWLEDGE_DIR / "sources"
REGISTRY = SOURCES_DIR / "_sources.json"

VALID_TYPES = {"book", "paper", "doc", "standard"}
VALID_RELIABILITY = {"high", "medium", "low"}


def main() -> int:
    ap = argparse.ArgumentParser(description="注册 Wiki 知识源骨架")
    ap.add_argument("--source-id", required=True,
                    help="全局唯一 id, 小写连字符, 如 book-meianxin-daolun")
    ap.add_argument("--type", required=True, choices=sorted(VALID_TYPES))
    ap.add_argument("--title", required=True)
    ap.add_argument("--authors", required=True,
                    help="分号分隔多人: '张三;李四'")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--venue", default="",
                    help="出版社/期刊/机构 (书目元数据)")
    ap.add_argument("--reliability", default="medium",
                    choices=sorted(VALID_RELIABILITY),
                    help="high=同行评审/官方 medium=教材 low=博客(不得为唯一出处)")
    args = ap.parse_args()

    if not args.source_id or args.source_id != args.source_id.lower() \
            or " " in args.source_id:
        ap.error("--source-id 必须是小写连字符形式 (无空格/大写)")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    registry = {}
    if REGISTRY.exists():
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if args.source_id in registry:
        print(f"[abort] source_id 已存在: {args.source_id}", file=sys.stderr)
        return 1

    registry[args.source_id] = {
        "type": args.type,
        "title": args.title,
        "authors": [a.strip() for a in args.authors.split(";") if a.strip()],
        "year": args.year,
        "venue": args.venue,
        "reliability": args.reliability,
        "registered_date": date.today().isoformat(),
    }
    REGISTRY.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    excerpt = SOURCES_DIR / f"{args.source_id}.md"
    if not excerpt.exists():
        excerpt.write_text(
            f"""---
source_id: {args.source_id}
type: {args.type}
title: "{args.title}"
authors: [{', '.join('"' + a + '"' for a in registry[args.source_id]['authors'])}]
year: {args.year}
venue: "{args.venue}"
reliability: {args.reliability}
---

# 摘录 — {args.title}

> 每个知识点片段一个 `##` 小节, 标题带定位锚点 ([P页码] / [§章节])。
> 只记原文要点与关键句, 不写自己的推论 —— 推论属于词条的判读基准段。

## [§待填] 第一个知识点
原文要点: <用一句话记录原文说了什么>
> 关键句: "<原文引用>"
""",
            encoding="utf-8")

    print(f"OK: 注册 {args.source_id} -> {REGISTRY}")
    print(f"    摘录骨架 -> {excerpt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
