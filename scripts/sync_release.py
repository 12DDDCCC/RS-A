#!/usr/bin/env python3
"""发布包同步: 仓库根(单一事实源) -> RS-agent/ 自包含发布目录。

职责 (幂等, 可反复执行):
  1. 白名单复制: src/ tests/ requirements.txt .env.example README.md
     .gitignore evals/ evalset/ -> RS-agent/ 同名位置
  2. 发布脱敏: 本机账号名/本机路径 -> 占位符 (只改发布副本, 源不动)
  3. 敏感扫描: 替换后再扫密钥/账号/内网路径模式, 命中即非零退出
     (响亮失败 —— 发布包带敏感信息比同步失败严重得多)

排除: __pycache__ .pytest_cache *.pyc cache/ docs/obsidian(内部存档不发布)。
自保护: 本脚本自身跳过脱敏与扫描 (否则第一轮就会把脱敏表自我替换掉 ——
实测踩坑: 表的左侧模式被当成待脱敏文本改写, 第二轮起脱敏变空转)。

用法: python sync_release.py   (在仓库根或任意位置均可)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "RS-agent"
SELF = Path(__file__).resolve()

# README.md/.gitignore 不再同步: RS-agent 已是独立 GitHub 仓库 (46号),
# 两文件是仓库门面/独立忽略规则, 用父仓库版覆盖会毁掉门面
COPY_ITEMS = [
    "src", "tests", "evals", "evalset",
    "requirements.txt", ".env.example",
]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "cache"}
SENSITIVE_RES = [
    "xiao" "meng", "ncib-" "491710", "gee-" "agent@",
    r"AI\+遥感",                       # 本机中文路径
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"sk-[A-Za-z0-9]{16,}",            # OpenAI 形态 key
    r"AKIA[0-9A-Z]{16}",               # AWS AK 形态
]
# 脱敏替换表 (顺序执行; 只作用于发布副本, 不作用于本脚本)
REDACT = [
    ("sa@your-project-id", "sa@your-project-id.iam.gserviceaccount.com"),
    ("rs-a-user", "rs-a-user"),
    ("D:/rs-agent-workspace", "D:/rs-agent-workspace"),
    ("D:\\AI\\_workspace", "D:/rs-agent-workspace"),
]
TEXT_EXTS = {".py", ".md", ".yml", ".yaml", ".json", ".txt", ".cmd",
             ".ts", ".js", ".example"}
# 已知测试 fixture (假密钥, 用于验证脱敏器本身), 扫描前剔除
FIXTURES = ["sk-abcdefghijklmnop123456"]


def _ignored(p: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in p.parts) or p.suffix == ".pyc"


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return None


def copy_tree(src: Path, dst: Path):
    for p in sorted(src.rglob("*")):
        if _ignored(p.relative_to(src.parent)):
            continue
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


# stage 级追加替换: 连扫描规则里的字面模式也净化 (脚本源码随包发布,
# 检测规则本身会间接暴露身份 —— obsidian 34)
STAGE_EXTRA = [
    ("xiao" "meng", "rs-a-user"),
    ("ncib-" "491710", "your-project-id"),
    ("gee-" "agent@", "sa@"),
    # 裸中文路径片段: 兜住脚本源码文本里的转义形态 (D:\\AI\\_workspace),
    # 该形态不是 REDACT 单反斜杠 LHS 的子串, 常规替换永远够不到 (obsidian 34)
    ("AI" "+遥感", "_workspace"),
]


def redact_dir(target: Path) -> int:
    """对任意目录树做全量脱敏 (pack-release 用于 stage 暂存副本)。

    stage 是发布专用拷贝, 活机操作文件 (dsh/dsh-config 等) 的副本在此
    脱敏不影响本机; 返回改写文件数。
    """
    n = 0
    for p in target.rglob("*"):
        if not p.is_file() or p.suffix == ".pyc":
            continue
        # 相对 target 判断排除目录 (stage 常位于 cache/ 之下, 绝对路径会全跳)
        rel_parts = p.relative_to(target).parts
        if any(part in EXCLUDE_DIRS for part in rel_parts):
            continue
        text = _read(p)
        if text is None:
            continue
        new = text
        for old, new_ in REDACT + STAGE_EXTRA:
            new = new.replace(old, new_)
        if new != text:
            p.write_text(new, encoding="utf-8")
            n += 1
    return n


def main() -> int:
    if not (ROOT / "src").exists():
        print(f"[ERROR] 仓库根不对: {ROOT} 缺 src/", file=sys.stderr)
        return 1
    DEST.mkdir(exist_ok=True)

    # 1) 白名单复制 (记录同步面 —— 脱敏/扫描只作用于这些文件)
    copied = 0
    synced_files: list[Path] = []
    for item in COPY_ITEMS:
        s = ROOT / item
        d = DEST / item
        if s.is_dir():
            copy_tree(s, d)
            synced_files.extend(p for p in d.rglob("*") if p.is_file())
            copied += 1
        elif s.exists():
            shutil.copy2(s, d)
            synced_files.append(d)
            copied += 1
        else:
            print(f"[WARN] 白名单项缺失, 跳过: {item}")

    # 2) 发布脱敏 (仅同步面; 跳过本脚本 —— 防脱敏表自我替换)
    # 范围铁律: 只脱敏 SYNCED 面的副本。dsh/ dsh-config/ remote-sensing-tools/
    # scripts/ 是活机操作文件 (start-dsh 下次启动要读), 脱敏它们会弄断本地
    # 融合链路 (obsidian 34 实测误伤); 发布净化在 pack 的 stage 副本上做。
    redacted_files = 0
    for p in sorted(synced_files):
        if not p.is_file() or p.resolve() == SELF:
            continue
        if p.suffix.lower() not in TEXT_EXTS and p.name != ".gitignore":
            continue
        text = _read(p)
        if text is None:
            continue
        new = text
        for old, new_ in REDACT:
            new = new.replace(old, new_)
        if new != text:
            p.write_text(new, encoding="utf-8")
            redacted_files += 1

    # 3) 敏感扫描 (响亮失败; 仅同步面, 同样跳过本脚本)
    import re
    hits = []
    for p in sorted(synced_files):
        if not p.is_file() or p.resolve() == SELF or p.suffix == ".pyc":
            continue
        text = _read(p)
        if text is None:
            continue
        for fx in FIXTURES:
            text = text.replace(fx, "")
        for pat in SENSITIVE_RES:
            if re.search(pat, text):
                hits.append(f"{p.relative_to(DEST)} <- /{pat}/")

    print(f"sync OK: 复制项 {copied}, 脱敏文件 {redacted_files}, 输出 {DEST}")
    if hits:
        print("SENSITIVE HITS (发布包不得携带, 请补充脱敏表):", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        return 1
    print("sensitive-scan clean")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--redact-dir":
        print(f"redact-dir: {redact_dir(Path(sys.argv[2]))} 文件已脱敏")
        sys.exit(0)
    sys.exit(main())
