"""Wiki 知识库构建器: 校验全部词条 + 重建 _index.json。

SPEC 见 src/knowledge/wiki/SPEC.md —— 本模块是规范的机器执行者:
任一词条不合格即整体构建失败 (响亮失败原则, 防坏内容静默入库)。

用法: python -m src.knowledge.wiki_build
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent


# 目录在每次调用时解析环境变量 —— 测试可把 wiki 指到临时目录
# (REMOTE_SENSING_WIKI_DIR / REMOTE_SENSING_SOURCES_DIR, 与 conftest 存储隔离同思路)
def _wiki_dir() -> Path:
    return Path(os.environ.get(
        "REMOTE_SENSING_WIKI_DIR", str(KNOWLEDGE_DIR / "wiki"))).resolve()


def _sources_dir() -> Path:
    return Path(os.environ.get(
        "REMOTE_SENSING_SOURCES_DIR", str(KNOWLEDGE_DIR / "sources"))).resolve()

DOMAINS = {"fundamentals", "physics", "image-processing",
           "analysis", "applications"}
TYPES = {"indices", "satellites", "sensors", "methods", "concept",
         "application-pack", "pitfalls"}

# SPEC §4.2 各 type 的强制正文章节 (标题允许带后缀括注, 故用 startswith)
REQUIRED_SECTIONS = {
    "indices": ["定义", "适用条件", "判读基准", "常见错误"],
    "satellites": ["波段表", "重访周期", "数据集ID"],
    "sensors": ["波段表", "重访周期", "数据集ID"],
    "methods": ["流程步骤", "输入输出", "精度参考", "常见失败模式"],
    "physics": ["公式推导", "遥感量纲", "常见误解"],
    "application-pack": ["数据组合", "指标集与阈值", "成果形态", "行业规范引用"],
    "concept": ["定义"],
}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z]{2,}")


class WikiValidationError(Exception):
    """构建期校验失败 (消息聚合了全部错误, 一次看全)。"""


@dataclass
class Entry:
    path: Path
    frontmatter: dict = field(default_factory=dict)
    body: str = ""
    errors: list[str] = field(default_factory=list)


def _parse_scalar(raw: str):
    """解析标量: 去引号/类型推断。"""
    raw = raw.strip()
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')) and len(raw) >= 2:
        return raw[1:-1]
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_inline_list(raw: str) -> list:
    """解析 [a, b, c] 行内列表。"""
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    return [_parse_scalar(p) for p in _split_top(inner)]


def _split_top(s: str) -> list[str]:
    """按逗号切分, 跳过引号与大括号内部。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def _parse_flow_value(raw: str):
    """解析 `- {k: v, k2: v2}` 与 `[a, b]` 流式值。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return _parse_inline_list(raw)
    if raw.startswith("{") and raw.endswith("}"):
        inner = raw.strip()[1:-1]
        out = {}
        for part in _split_top(inner):
            k, _, v = part.partition(":")
            out[k.strip()] = _parse_flow_value(v) if v.strip().startswith(("[", "{")) else _parse_scalar(v)
        return out
    return _parse_scalar(raw)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 markdown 头部 --- 包围的受限 YAML 子集。

    支持: 标量 / `- item` 列表 / [a, b] 行内表 / `- {k: v}` 字典项。
    规范外语法直接抛错 —— 词条是受控产物, 不做宽松容错。
    """
    if not text.startswith("---"):
        raise WikiValidationError("缺少 frontmatter (必须以 --- 开头)")
    end = text.find("\n---", 3)
    if end < 0:
        raise WikiValidationError("frontmatter 未闭合 (缺第二个 ---)")
    fm_text = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("-").lstrip("\n")

    fm: dict = {}
    current_key = None
    for lineno, line in enumerate(fm_text.splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_key is not None:
            stripped = line.strip()
            if not stripped.startswith("-"):
                raise WikiValidationError(f"frontmatter 第{lineno}行: 缩进行必须是 '- ' 列表项")
            item = _parse_flow_value(stripped[1:])
            existing = fm[current_key]
            if not isinstance(existing, list):
                existing = []
            existing.append(item)
            fm[current_key] = existing
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise WikiValidationError(f"frontmatter 第{lineno}行: 缺少冒号分隔")
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            fm[key] = []          # 空值先置空列表, 后续缩进列表项会填充
        else:
            fm[key] = _parse_flow_value(value)
    return fm, body


def parse_spec_version() -> str:
    spec = _wiki_dir() / "SPEC.md"
    text = spec.read_text(encoding="utf-8")
    m = re.search(r"WIKI_SPEC_VERSION\s*=\s*([\d.]+)", text)
    if not m:
        raise WikiValidationError("SPEC.md 未声明 WIKI_SPEC_VERSION")
    return m.group(1)


def load_sources_registry() -> dict:
    reg = _sources_dir() / "_sources.json"
    if not reg.exists():
        raise WikiValidationError(f"来源注册表不存在: {reg}")
    data = json.loads(reg.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def discover_entries() -> list[Entry]:
    entries = []
    for md in sorted(_wiki_dir().rglob("*.md")):
        if md.name == "SPEC.md":
            continue
        fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
        entries.append(Entry(path=md, frontmatter=fm, body=body))
    return entries


def validate_entries(entries: list[Entry], sources_reg: dict) -> dict[str, dict]:
    """全量校验, 返回可入索引的词条信息; 有任何错误则抛异常。"""
    all_errors: list[str] = []
    valid: dict[str, dict] = {}
    ids_by_file: dict[str, str] = {}

    for e in entries:
        rel = e.path.relative_to(_wiki_dir()).as_posix()
        tag = f"[{rel}]"
        fm = e.frontmatter
        eid = fm.get("id")
        # id 必须等于文件名 stem
        if eid != e.path.stem:
            all_errors.append(f"{tag} id({eid}) 与文件名({e.path.stem})不一致")
        # 枚举域校验
        if fm.get("domain") not in DOMAINS:
            all_errors.append(f"{tag} domain 非法: {fm.get('domain')!r}")
        if fm.get("type") not in TYPES:
            all_errors.append(f"{tag} type 非法: {fm.get('type')!r}")
        # aliases: 中文+英文 各至少一条
        aliases = fm.get("aliases")
        if not isinstance(aliases, list) or len(aliases) < 2:
            all_errors.append(f"{tag} aliases 至少 2 条 (中文名+英文), 实得 {aliases!r}")
        else:
            if not any(isinstance(a, str) and _CJK.search(a) for a in aliases):
                all_errors.append(f"{tag} aliases 缺中文名")
            if not any(isinstance(a, str) and _ASCII_WORD.search(a) for a in aliases):
                all_errors.append(f"{tag} aliases 缺英文 (全称或缩写)")
        themes = fm.get("themes")
        if not isinstance(themes, list) or not themes or \
                not all(isinstance(t, str) and t for t in themes):
            all_errors.append(f"{tag} themes 必须非空字符串列表")
        if not isinstance(fm.get("verified"), bool):
            all_errors.append(f"{tag} verified 必须显式 true/false")
        # sources: 注册表内存在 + 锚点齐备 + 低可信源不得支撑 verified 词条
        srcs = fm.get("sources")
        if not isinstance(srcs, list) or not srcs:
            all_errors.append(f"{tag} sources 至少 1 条 (无出处不入库)")
        else:
            for s in srcs:
                if not isinstance(s, dict) or "source_id" not in s or "anchor" not in s:
                    all_errors.append(f"{tag} sources 项缺 source_id/anchor: {s!r}")
                    continue
                meta = sources_reg.get(s["source_id"])
                if meta is None:
                    all_errors.append(f"{tag} source_id 未注册: {s['source_id']}")
                elif fm.get("verified") is True and meta.get("reliability") == "low":
                    all_errors.append(
                        f"{tag} verified 词条引用 low 可信来源: {s['source_id']}")
        # 强制章节
        t = fm.get("type")
        for sec in REQUIRED_SECTIONS.get(t, []):
            pattern = re.compile(rf"^##\s+{re.escape(sec)}", re.MULTILINE)
            if not pattern.search(e.body):
                all_errors.append(f"{tag} type={t} 缺必填章节: ## {sec}")
        # 关联词条 [[x]] 引用收集 (解析完所有 id 后二次核验)
        if eid:
            ids_by_file[rel] = eid
            valid[eid] = {
                "file": rel,
                "title": fm.get("title"),
                "type": t,
                "domain": fm.get("domain"),
                "aliases": aliases or [],
                "themes": themes or [],
                "verified": fm.get("verified"),
                "template_hint": fm.get("template_hint"),
            }

    # 二次核验: related 双向互指 + [[x]] 引用必须可解析
    by_id = {fm.get("id"): e for e, fm in
             ((e, e.frontmatter) for e in entries)}
    for e in entries:
        rel = e.path.relative_to(_wiki_dir()).as_posix()
        tag = f"[{rel}]"
        eid = e.frontmatter.get("id")
        links = set(re.findall(r"\[\[([a-z0-9\-]+)\]\]", e.body))
        for target in sorted(links):
            if target not in by_id:
                all_errors.append(f"{tag} [[{target}]] 不存在 (悬空链接)")
        related = e.frontmatter.get("related") or []
        for rid in related:
            if rid not in by_id:
                all_errors.append(f"{tag} related[{rid}] 不存在")
            elif eid and rid in by_id and eid in by_id:
                back = by_id[rid].frontmatter.get("related") or []
                if eid not in back:
                    all_errors.append(f"{tag} related 单向: {rid}.related 未回指 {eid}")

    if all_errors:
        raise WikiValidationError(
            f"Wiki 校验失败 ({len(all_errors)} 处):\n" +
            "\n".join(f"  - {m}" for m in all_errors))
    return valid


def build_index(valid_entries: dict[str, dict]) -> dict:
    theme_words: dict = {}
    tw_path = _wiki_dir() / "_theme_words.json"
    if tw_path.exists():
        theme_words = json.loads(tw_path.read_text(encoding="utf-8"))
    # theme_words 引用的词条 id 必须真实存在 (响亮失败; 下划线前缀键是元信息)
    bad = [(kw, i) for kw, ids in theme_words.items()
           if not kw.startswith("_") and isinstance(ids, list)
           for i in ids if i not in valid_entries]
    if bad:
        raise WikiValidationError(
            "theme_words 引用了不存在的词条: "
            + ", ".join(f"{kw}->{i}" for kw, i in bad))
    return {
        "spec_version": parse_spec_version(),
        "generated_at": date.today().isoformat(),
        "entries": valid_entries,
        "theme_words": theme_words,
    }


def main() -> int:
    sources_reg = load_sources_registry()
    entries = discover_entries()
    valid = validate_entries(entries, sources_reg)
    index = build_index(valid)
    index_path = _wiki_dir() / "_index.json"
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wiki_build OK: {len(valid)} 词条 -> {index_path}")
    for eid, info in sorted(valid.items()):
        print(f"  - {eid:24s} [{info['domain']}/{info['type']}] "
              f"{'verified' if info['verified'] else 'UNVERIFIED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
