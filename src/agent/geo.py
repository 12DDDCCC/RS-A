"""P0-1 地名解析: 把 "北京"/"成都市" 这类口语地名转成城市 bbox。

设计蓝图: 面向没用过 GEE 的普通人, 用户说"北京""成都"即可,
不该要求手填经纬度。防幻觉铁律: 只认知识库核实过的种子城市
(cn_places.json, 31 条), 匹配不到返回 None 交调用方反问, 绝不猜坐标;
歧义名 (如 "朝阳" 同时是长春的区和辽宁的市) 返回 ambiguous + 候选, 不拍板。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# 城市种子表: src/knowledge/cn_places.json (与 datasets.json 同层, 便于统一管理)
_PLACES_PATH = Path(__file__).parent.parent / "knowledge" / "cn_places.json"

# 行政后缀表 (长在前, 防止 "自治区" 被短的 "区" 先截走)
_SUFFIXES = ("特别行政区", "自治区", "自治州", "地区", "盟", "省", "市", "区")


@dataclass
class PlaceResult:
    """地名解析结果。

    confidence: "high" 唯一命中; "ambiguous" 多候选 (ambiguous_cities 列出),
    上层应反问用户选哪个, 而不是替用户猜。
    """

    name: str
    bbox: dict  # {"lon_min":..,"lat_min":..,"lon_max":..,"lat_max":..}
    confidence: str  # "high" | "ambiguous"
    ambiguous_cities: list[str] = field(default_factory=list)


def _strip_suffix(name: str) -> str:
    """剥离行政后缀: "北京市"->"北京", "内蒙古自治区"->"内蒙古"。"""
    for suf in _SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


@lru_cache(maxsize=1)
def _load_index() -> dict[str, list[dict]]:
    """构建 归一化名 -> [条目,...] 索引。

    条目的 name 和全部 aliases 归一化后入库; 同一归一化名挂多个条目
    即歧义 (列表长度 > 1), 由 resolve_place 统一处理。
    """
    with open(_PLACES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    index: dict[str, list[dict]] = {}
    for entry in data["places"]:
        for key in [entry["name"], *entry.get("aliases", [])]:
            bucket = index.setdefault(_strip_suffix(key), [])
            if entry not in bucket:
                bucket.append(entry)
    return index


@lru_cache(maxsize=1)
def _load_ambiguous() -> dict[str, list[str]]:
    """歧义别名表: 口语名 -> 候选城市列表 (独立于主表, 见 resolve_place 1.5)。"""
    with open(_PLACES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {k: list(v) for k, v in data.get("ambiguous_aliases", {}).items()}


def _result(entries: list[dict]) -> PlaceResult:
    """由命中的 (去重) 条目生成结果: 唯一 -> high, 多个 -> ambiguous 不猜。

    ambiguous 时 name/bbox 取首个候选仅为占位 (dataclass 必填), 以
    ambiguous_cities 为准。
    """
    if len(entries) == 1:
        return PlaceResult(
            name=entries[0]["name"], bbox=dict(entries[0]["bbox"]), confidence="high"
        )
    return PlaceResult(
        name=entries[0]["name"],
        bbox=dict(entries[0]["bbox"]),
        confidence="ambiguous",
        ambiguous_cities=[e["name"] for e in entries],
    )


def resolve_place(text: str) -> PlaceResult | None:
    """把口语地名解析为城市 bbox, 解析不到返回 None (调用方负责反问)。

    匹配顺序: 全名精确匹配 (含别名, 行政后缀归一化) > 子串包含匹配
    (取最长命中: "北京市朝阳区"->北京, "上海周边"->上海)。
    """
    text = text.strip()
    if not text:
        return None
    index = _load_index()

    # 1) 全名精确匹配: "北京市" 归一化为 "北京" 后查索引
    exact = index.get(_strip_suffix(text), [])
    if exact:
        return _result(exact)

    # 1.5) 歧义别名表 (G3): "朝阳" 这类同名异地名不进主表 (会破坏 contains
    #      截断语义), 独立成表; 命中即返回 ambiguous + 候选反问, 不猜。
    amb = _load_ambiguous().get(_strip_suffix(text))
    if amb:
        return PlaceResult(
            name=amb[0],
            bbox={"lon_min": 0, "lat_min": 0, "lon_max": 0, "lat_max": 0},
            confidence="ambiguous",
            ambiguous_cities=list(amb),
        )

    # 2) 子串包含匹配: 找所有是 text 子串的 key, 只认最长的一组
    #    (最长优先防 "吉林市" 被 "吉林" 之类短名抢先截走)
    hits = [k for k in index if k in text]
    if not hits:
        return None
    max_len = max(len(k) for k in hits)
    entries: list[dict] = []
    for k in hits:
        if len(k) == max_len:
            for e in index[k]:
                if e not in entries:
                    entries.append(e)
    return _result(entries)


# 区县级行政后缀 (长在前防短后缀先截; "新区"/"林区"由"区"自然覆盖)
_DISTRICT_SUFFIXES = ("自治县", "自治旗", "区", "县", "旗")# head 中的城市/省级锚点: "北京市朝阳区" 只取最后一个锚点后的 "朝阳" 作候选
_ADMIN_ANCHORS = ("市", "省")
# 两字候选的常见非地名干扰词 (指示/范围泛称)
_STOPWORDS = {"这个", "那个", "某", "全县", "全区", "市区", "郊区", "城区", "县区",
              "地区", "区域", "小区", "街区", "园区", "景区", "校区", "社区"}
# 常见任务动词: 作为前导出现时是强切分锚 ("分析江宁区" -> 江宁)
_VERB_ANCHORS = {"分析", "监测", "评估", "看看", "查看", "查查", "研究", "对比",
                 "统计", "提取", "识别", "分类", "计算", "观察", "调查", "检测",
                 "出图", "制作", "生成", "遥感", "研究下", "分析下"}


def guess_district(text: str) -> str | None:
    """从文本提取区县级行政区名 (如 "江宁区"/"曲水县"), 供 GAUL 动态取边界。

    区县级默认精度: 用户未给经纬度、且城市表未命中时, 识别 "2-6 字 +
    区/县/旗 后缀" 的名字 —— 边界交给生成代码从 GEE FAO/GAUL/2015/level2
    按 NAME_2 (拼音) 动态解析, 本地不存区县表。

    切分优先级 (确定性, 不猜归属省市; 重名区县由生成侧按任务上下文收窄):
      1. 前导恰为任务动词 ("分析江宁区"->江宁) — 强锚
      2. 前导为空 ("江宁区" 纯地名 / place 字段)
      3. 前导 ≤2 字非动词 ("的/对/看" 等短词)
    名字不得落在城市主表/歧义别名域; 连写的 "XX市+区县" 先剥行政锚;
    两字候选排除非地名干扰词。
    返回带后缀全名 (与 GAUL 对应时去后缀转拼音)。
    """
    text = text.strip()
    if not text:
        return None
    index = _load_index()
    ambiguous = _load_ambiguous()

    def _clean(cand: str) -> bool:
        stem = _strip_suffix(cand)
        if stem in index or stem in ambiguous:
            return False  # 城市/歧义域让上层解析优先
        if len(cand) <= 2 and cand in _STOPWORDS:
            return False
        return True

    for suf in _DISTRICT_SUFFIXES:
        i = text.find(suf)
        while i != -1:
            head = text[:i]
            # 连写的 "城市+区县" 先剥行政锚 ("北京市朝阳" -> "朝阳")
            for anchor in _ADMIN_ANCHORS:
                p = head.rfind(anchor)
                if p != -1:
                    head = head[p + len(anchor):]
                    break
            verb_hit = bare_hit = short_hit = None
            for n in range(min(6, len(head)), 1, -1):
                cand = head[-n:]
                prefix = head[:-n]
                if not _clean(cand):
                    continue
                if prefix in _VERB_ANCHORS:
                    verb_hit = cand + suf       # 动词锚定最强, 长名优先
                elif not prefix and bare_hit is None:
                    bare_hit = cand + suf       # 纯地名 (最长)
                elif len(prefix) <= 2 and short_hit is None:
                    short_hit = cand + suf       # 短前导 (最长)
            chosen = verb_hit or bare_hit or short_hit
            if chosen:
                return chosen
            i = text.find(suf, i + 1)
    return None

def district_pinyin(district: str) -> str:
    """区县名 -> geoBoundaries 匹配用的全拼 (江宁区 -> Jiangning)。

    去行政后缀后逐字转拼音、首字母大写; 数据侧 shapeName 是全拼连写带
    后缀 (Jiangningxian/qu/qi), 故用 stringContains(去后缀全拼) 模糊命中。
    """
    from pypinyin import lazy_pinyin

    stem = district
    for suf in _DISTRICT_SUFFIXES:
        if stem.endswith(suf) and len(stem) > len(suf):
            stem = stem[: -len(suf)]
            break
    py = "".join(lazy_pinyin(stem))
    return py[:1].upper() + py[1:] if py else ""


def resolve_district_bbox(place: str, user_id: str) -> dict | None:
    """区县名 -> geoBoundaries 行政区 bbox (一次性子进程查询, 出网 GEE)。

    设计 (确定性层才有裁决权): 区县边界解析不交给 LLM 写代码 —— 统一由
    本函数查 WM/geoLab/geoBoundaries/600/ADM2 (中国 2370 条区县级, 实测
    核实), 命中即转 bbox, 之后的生成/沙箱/执行与城市模式完全同构。

    为什么 subprocess: earthengine-api 的 ee.Initialize 是进程级全局态,
    在主管线进程里初始化后再跑沙箱会残留失效会话 (实测 Please authorize);
    一次性子进程干净退出, 主进程 ee 状态零污染。
    唯一命中 -> bbox; 多条(重名)/零条/任何异常 -> None (上层转人话错误)。
    """
    import subprocess
    import sys

    # frozen (exe): sys.executable 是 RS-A.exe 自身, 子进程方案失效 ——
    # 改进程内直调 (ee 全局态残留可接受: 后续平台执行会重新 Initialize)
    if getattr(sys, "frozen", False):
        try:
            from src.agent.district_query_main import query as _query

            code, out = _query(place, user_id)
            if code == 0 and out:
                return json.loads(out)
            from src.runtime.obs import log_event

            log_event("district_query_error", stage="geo",
                      detail=f"frozen in-process exit={code}")
            return None
        except Exception as e:   # 观测旁路: 失败原因必须可追查 (不再静默)
            try:
                from src.runtime.obs import log_event

                log_event("district_query_error", stage="geo",
                          detail=f"frozen in-process {type(e).__name__}: {e}")
            except Exception:
                pass
            return None

    entry = Path(__file__).parent / "district_query_main.py"
    try:
        r = subprocess.run(
            [sys.executable, str(entry), place, user_id],
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode != 0:
            if r.returncode != 1:  # 1=未命中(正常), 其他码记观测
                from src.runtime.obs import log_event

                log_event("district_query_error", stage="geo",
                          detail=(r.stderr or "")[:200])
            return None
        return json.loads(r.stdout.strip())
    except Exception:
        return None
