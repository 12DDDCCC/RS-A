"""P1-3 结果可读性测试: caption 模板 + JPEG 横幅叠加 + 字体降级 + 栅格色带。

守护:
  - 蓝图铁律: 图说首句必须是 "分析区域：..." (区域框错用户能发现)
  - 字体缺失绝不崩 (退化英文占位, 输出仍是合法 JPEG)
  - 横幅叠加必须真实生效 (带/不带 caption 的输出字节不同)
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.io import output as output_mod
from src.io.caption import build_caption
from src.io.output import to_jpeg


# ---------- build_caption 纯模板 ----------

def test_caption_first_line_states_place():
    """首句铁律: 分析区域必须出现且带地名。"""
    c = build_caption("vegetation", "北京", {})
    assert c.splitlines()[0] == "分析区域：北京"


def test_caption_empty_place_degrades():
    """无 place -> 退化为 "见任务描述", 不猜地名。"""
    c = build_caption("vegetation", "", {})
    assert c.splitlines()[0] == "分析区域：见任务描述"


def test_caption_vegetation_guide():
    """vegetation 模板必须讲清楚植被怎么看。"""
    c = build_caption("vegetation", "成都", {})
    assert "植被" in c
    assert "绿色" in c


def test_caption_unknown_task_uses_default_guide():
    c = build_caption("weird_type", "北京", {})
    assert "颜色深浅" in c


def test_caption_metrics_ndvi_mean():
    """metrics 有数值型 ndvi_mean -> 注入均值 (两位小数)。"""
    c = build_caption("vegetation", "北京", {"ndvi_mean": 0.4231})
    assert "0.42" in c
    # 脏数据 (字符串) 不崩也不注入
    c2 = build_caption("vegetation", "北京", {"ndvi_mean": "oops"})
    assert "oops" not in c2


# ---------- to_jpeg 横幅叠加 ----------

@pytest.fixture
def src_jpeg(tmp_path):
    """PIL 生成一张真实源 JPEG (Mock 平台产物同款路径: 直拷分支)。"""
    import numpy as np
    from PIL import Image

    arr = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="JPEG")
    p = tmp_path / "src.jpg"
    p.write_bytes(buf.getvalue())
    return p


@pytest.fixture
def redir_cache(tmp_path, monkeypatch):
    """输出目录重定向到 tmp, 防污染真实 cache/。"""
    monkeypatch.setattr(output_mod, "CACHE_DIR", tmp_path / "out")
    return tmp_path / "out"


def test_jpeg_caption_overlay_valid_and_differs(src_jpeg, redir_cache):
    """带 caption: 仍是合法 JPEG (FFD8), 且与不带 caption 的直拷字节不同。"""
    plain = to_jpeg(str(src_jpeg), out_name="plain")
    with_cap = to_jpeg(str(src_jpeg), out_name="capped",
                       caption="分析区域：北京\n绿色越深表示植被越茂盛。", place="北京")
    pb, cb = Path(plain).read_bytes(), Path(with_cap).read_bytes()
    assert pb.startswith(b"\xff\xd8\xff")  # 直拷原样
    assert cb.startswith(b"\xff\xd8\xff")  # 叠加后仍是合法 JPEG
    assert cb != pb  # 横幅叠加真实生效


def test_jpeg_caption_grows_height(src_jpeg, redir_cache):
    """底部横幅 -> 输出图高度增加。"""
    from PIL import Image

    plain = to_jpeg(str(src_jpeg), out_name="p2")
    with_cap = to_jpeg(str(src_jpeg), out_name="c2",
                       caption="分析区域：北京", place="北京")
    assert Image.open(with_cap).height > Image.open(plain).height


def test_font_missing_fallback_no_crash(src_jpeg, redir_cache, monkeypatch):
    """中文字体全部缺失 -> 不崩, 退化为英文占位, 输出仍是合法 JPEG。"""
    monkeypatch.setattr(output_mod, "_CN_FONTS", ("Z:/不存在/font.ttc",))
    out = to_jpeg(str(src_jpeg), out_name="nofont",
                  caption="分析区域：北京", place="北京")
    data = Path(out).read_bytes()
    assert data.startswith(b"\xff\xd8\xff")
    # PIL 能正常打开 (不是截断/损坏文件)
    from PIL import Image

    Image.open(out).verify()


def test_font_missing_ascii_place_uses_place(src_jpeg, redir_cache, monkeypatch):
    """字体缺失 + place 是 ASCII -> 占位 "Analysis: {place}" (路径不崩即可)。"""
    monkeypatch.setattr(output_mod, "_CN_FONTS", ())
    out = to_jpeg(str(src_jpeg), out_name="nofont2",
                  caption="分析区域：Beijing", place="Beijing")
    assert Path(out).read_bytes().startswith(b"\xff\xd8\xff")


# ---------- GeoTIFF -> 色带 ----------

def test_raster_to_jpeg_has_colorbar(tmp_path, redir_cache, monkeypatch):
    """最小 GeoTIFF -> 可视化 JPEG: 合法 + fig.colorbar 真实被调用。"""
    import matplotlib.figure
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    # spy: 直接验证出图时挂了色带 (宽高比断言太依赖布局参数, 弃用)
    calls = []
    orig = matplotlib.figure.Figure.colorbar

    def _spy(self, mappable, *a, **kw):
        calls.append(1)
        return orig(self, mappable, *a, **kw)

    monkeypatch.setattr(matplotlib.figure.Figure, "colorbar", _spy)

    h = w = 32
    tif = tmp_path / "mini.tif"
    data = np.linspace(0, 1, h * w, dtype=np.float32).reshape(h, w)
    profile = {
        "driver": "GTiff", "height": h, "width": w, "count": 1,
        "dtype": "float32",
        "transform": from_bounds(116.0, 39.0, 117.0, 40.0, w, h),
    }
    with rasterio.open(tif, "w", **profile) as ds:
        ds.write(data, 1)

    out = to_jpeg(str(tif), task_type="vegetation", out_name="raster_out")
    assert Path(out).read_bytes().startswith(b"\xff\xd8\xff")
    assert calls, "GeoTIFF 可视化输出必须带色带 (fig.colorbar)"


# ---------- jobs 集成: done 后 state 存 caption ----------

def test_run_job_stores_caption(tmp_path, monkeypatch):
    """任务成功 -> state["caption"] 生成并入库 (首句铁律)。"""
    from src.io import store_credentials
    from src.runtime import jobs as jobs_mod

    monkeypatch.setenv("REMOTE_SENSING_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(output_mod, "CACHE_DIR", tmp_path / "out")
    monkeypatch.setattr(jobs_mod, "RUNS_DIR", tmp_path / "runs")
    s = jobs_mod.JobStore(db_path=tmp_path / "jobs.db")
    monkeypatch.setattr(jobs_mod, "store", s)
    store_credentials("cap_u", {"pie_token": "t"})

    cb = {
        "clarify": lambda p: '{"task_type":"vegetation","clarified":"北京植被","need_clarify":false,"clarify_question":""}',
        "generate": lambda p: "ndvi = (B8 - B4) / (B8 + B4)\nimg = load('COPERNICUS/S2_SR_HARMONIZED')\nresult = img",
        "review": lambda p: "APPROVED\nok",
        "diagnose": lambda p: '{"diagnosis":"ok","reason":"正常","should_retry":false,"retry_hint":""}',
    }
    task_id = s.create("cap_u", {
        "user_input": "北京植被",
        "region": {"lon_min": 116.0, "lat_min": 39.0, "lon_max": 117.0, "lat_max": 40.0},
        "place": "北京",  # 生产链路 main.py 已接线 (审查修复: place 三处断链)
        "user_id": "cap_u", "retry_count": 0, "max_retries": 2,
    })
    jobs_mod.run_job(task_id, cb)

    rec = s.get(task_id)
    assert rec["status"] == "done"
    state = json.loads(rec["state_json"])
    # 首句铁律: 真实地名 (place 已进 AgentState 并流到 final)
    assert state["caption"].splitlines()[0] == "分析区域：北京"
    assert "植被" in state["caption"]  # vegetation 读图说明
