"""结果输出: 把云端执行结果转成 JPEG 供用户查看。

决策6: MVP 输出 JPEG, 用户查看用。
- 若产物已是 JPEG: 校验魔数后直拷; 给了 caption 则在底部叠加图说横幅 (PIL)。
- 若是 GeoTIFF 栅格: 用 rasterio 读 + matplotlib 出可视化 JPEG (带标题 + 右侧色带)。
- JPEG 不含坐标/凭证等敏感信息 (普通人查看用)。
- P1-3: 中文字体缺失绝不崩, 退化英文占位。
"""
from __future__ import annotations
from src.paths import cache_root as paths_cache_root

import io
from pathlib import Path

import numpy as np

CACHE_DIR = paths_cache_root()

# 中文字体候选 (Windows 微软雅黑优先, 黑体兜底; 都缺失则退化为英文占位)
_CN_FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")

# 横幅左右留白 (像素)
_BANNER_PAD = 12


def to_jpeg(source_path: str, task_type: str = "", out_name: str | None = None,
            caption: str = "", place: str = "", out_dir: Path | None = None) -> str:
    """把执行产物转成 JPEG。

    Args:
        source_path: 源产物路径 (JPEG 或 GeoTIFF)。
        task_type: 任务类型 (影响配色, 如 vegetation 用绿调)。
        out_name: 输出文件名 (不含扩展名); 省略则用源文件名。
        caption: 图说 (P1-3); 非空时在 JPEG 直拷产物底部叠加白底黑字横幅。
        place: 分析区域地名 (仅用于字体缺失时的英文占位 "Analysis: {place}")。
        out_dir: 输出目录; None 用默认 CACHE_DIR (jobs 层传任务目录实现按任务隔离)。

    Returns:
        输出 JPEG 路径。
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(f"源产物不存在: {source_path}")

    suffix = src.suffix.lower()
    out_name = out_name or src.stem
    out_path = (out_dir or CACHE_DIR) / f"{out_name}.jpg"
    # parents=True: 自定义 out_dir (如 cache/runs/{task_id}/) 可能整链不存在
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 源已是 JPEG -> 校验魔数后直拷 (防占位/损坏字节以假乱真);
    # 给了 caption 则先叠加图说横幅 (P1-3: 直拷路径原本无任何标注)
    if suffix in (".jpg", ".jpeg"):
        data = src.read_bytes()
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError(f"源文件不是合法 JPEG (魔数校验失败): {source_path}")
        if caption.strip():
            return _jpeg_with_banner(src, out_path, caption, place)
        out_path.write_bytes(data)
        return str(out_path)

    # 栅格 (GeoTIFF 等) -> 可视化出图
    if suffix in (".tif", ".tiff", ".geotiff"):
        return _raster_to_jpeg(src, task_type, out_path)

    # 其它: 不支持的格式直接报错 (不假装成功)
    raise ValueError(f"不支持的源格式: {suffix} (源: {source_path})")


def _raster_to_jpeg(src: Path, task_type: str, out_path: Path) -> str:
    """用 rasterio 读栅格, matplotlib 出可视化 JPEG (标题 + 右侧色带)。"""
    # 延迟导入 (output.py 可能被无 rasterio 环境调用)
    import matplotlib
    matplotlib.use("Agg")  # 无 GUI 后端
    import matplotlib.pyplot as plt
    import rasterio

    with rasterio.open(src) as ds:
        data = ds.read(1, masked=True)  # 读第一波段, 处理 nodata

    # 按任务选 colormap
    cmap = _cmap_for_task(task_type)

    # 中文标题需要中文字体 (默认 DejaVu Sans 渲染中文是豆腐块);
    # 字体族缺失 matplotlib 只告警不崩, rc_context 用完即还原不污染全局
    rc = {
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
    }
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(8, 8), dpi=100)
        # 归一化显示 (按百分位裁剪极端值, 提升可读性)
        vmin, vmax = np.nanpercentile(data.compressed(), [2, 98])
        im = ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_title(_title_for_task(task_type), fontsize=12)
        ax.axis("off")
        fig.colorbar(im, ax=ax)  # 右侧色带: 告诉用户"颜色深浅=数值大小"
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="jpeg", bbox_inches="tight", dpi=100)
        plt.close(fig)
    out_path.write_bytes(buf.getvalue())
    return str(out_path)


# ---------- P1-3: JPEG 直拷路径的图说横幅叠加 ----------

def _load_cn_font(size: int):
    """尝试加载中文字体; 全部缺失返回 None (调用方退化英文, 绝不因字体崩)。"""
    from PIL import ImageFont

    for path in _CN_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _wrap_text(draw, text: str, font, max_w: int) -> list[str]:
    """按像素宽度逐字符换行 (中文无空格, 不能按词折行)。"""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            # 首字符必收 (防极端窄图死循环)
            if not cur or draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def _jpeg_with_banner(src: Path, out_path: Path, caption: str, place: str) -> str:
    """在 JPEG 底部叠加白底黑字图说横幅。

    中文字体缺失时退化英文占位 "Analysis: {place}" (PIL 默认字体画不了
    中文, place 含非 ASCII 时用通用占位), 保证任何环境不崩。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(src).convert("RGB")  # 灰度/RGBA 统一转 RGB 再贴
    w, h = img.size

    font_size = max(16, min(w // 32, 48))
    font = _load_cn_font(font_size)
    if font is not None:
        text = caption
    else:
        text = f"Analysis: {place}" if place and place.isascii() else "Analysis Result"
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:  # Pillow < 10.1 的 load_default 无 size 参数
            font = ImageFont.load_default()

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    ascent, descent = font.getmetrics()
    line_h = ascent + descent + 2
    lines = _wrap_text(probe, text, font, w - 2 * _BANNER_PAD)

    banner_h = 2 * _BANNER_PAD + len(lines) * line_h
    canvas = Image.new("RGB", (w, h + banner_h), "white")
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    y = h + _BANNER_PAD
    for ln in lines:
        draw.text((_BANNER_PAD, y), ln, fill="black", font=font)
        y += line_h

    canvas.save(out_path, format="JPEG", quality=92)
    return str(out_path)


def _cmap_for_task(task_type: str) -> str:
    """按任务类型选配色。"""
    return {
        "vegetation": "YlGn",
        "water": "Blues",
        "land_cover": "tab10",
        "change_detection": "RdYlGn_r",
    }.get(task_type, "viridis")


def _title_for_task(task_type: str) -> str:
    return {
        "vegetation": "植被覆盖分析结果",
        "water": "水体提取结果",
        "land_cover": "土地覆盖分类结果",
        "change_detection": "变化检测结果",
    }.get(task_type, "遥感分析结果")
