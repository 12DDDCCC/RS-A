# -*- coding: utf-8 -*-
"""区县边界查询子进程入口 (geo.resolve_district_bbox 经 subprocess 调用)。

为什么独立进程: earthengine-api 的 ee.Initialize 是进程级全局态,
主进程里解析区县边界后再跑沙箱会残留失效会话 (实测 Please authorize)。
本脚本一次性: 初始化 -> 查 geoBoundaries -> 打印 bbox JSON 退出。

用法: python -m src.agent.district_query_main <区县名> <user_id>
stdout: {"lon_min":..,"lat_min":..,"lon_max":..,"lat_max":..} 或空(未命中)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 直接以文件路径运行时锚定项目根 (与 RS-agent/scripts 同款手法)
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def query(place: str, user_id: str) -> tuple[int, str]:
    """核心查询: 返回 (exit_code, stdout文本)。frozen 下进程内直调。

    进程内直调的代价是 ee 全局态残留 —— 可接受: 后续每次平台执行都会
    重新 ee.Initialize(用户凭证), 残留会话被覆盖 (GEE 适配器契约)。
    """
    from src.agent.geo import district_pinyin
    from src.io.credentials import load_credentials
    from src.platform.gee_adapter import _build_credentials

    import ee

    py = district_pinyin(place)
    if not py:
        return 1, ""
    creds_data = load_credentials(user_id)
    creds = _build_credentials(creds_data)
    project = creds_data.get("gee_project")
    if project:
        ee.Initialize(creds, project=project)
    else:
        ee.Initialize(creds)
    feats = (
        ee.FeatureCollection("WM/geoLab/geoBoundaries/600/ADM2")
        .filter(ee.Filter.eq("shapeGroup", "CHN"))
        .filter(ee.Filter.stringContains("shapeName", py))
    )
    if feats.size().getInfo() != 1:
        return 1, ""
    box = feats.first().geometry().bounds().getInfo()["coordinates"][0]
    lons = [p[0] for p in box]
    lats = [p[1] for p in box]
    return 0, json.dumps({
        "lon_min": min(lons), "lat_min": min(lats),
        "lon_max": max(lons), "lat_max": max(lats),
    })


def main() -> int:
    place, user_id = sys.argv[1], sys.argv[2]
    code, out = query(place, user_id)
    if out:
        print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
