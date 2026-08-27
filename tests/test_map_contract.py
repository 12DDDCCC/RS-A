"""制图契约测试: 图例/莫兰迪配色/比例尺指北针/标准工作流顺序。

模板与自由生成契约的硬性约束, 违反即基线红 —— 防止后续改动悄悄
丢掉正规成果图要素 (图例自检脚本曾在 dsh 会话清理中丢失)。
"""
from __future__ import annotations

from src.agent.prompts import PROMPT_VERSION
from src.codegen.generator import GENERATOR_SYSTEM_PROMPT
from src.codegen.templates import extract_params, render


def _code() -> str:
    params = extract_params("land_cover", "南京市2024年土地覆盖分类")
    assert params is not None
    return render(params)


def test_template_renders_and_compiles():
    code = _code()
    assert code and "OUTPUT_JPEG" in code
    compile(code, "<landcover_v1>", "exec")  # 语法错误直接抛


def test_legend_elements():
    code = _code()
    for token in ("ListedColormap", "Patch(", "plt.legend(", "font_manager"):
        assert token in code, token


def test_morandi_palette():
    code = _code()
    for token in ("#2e4a62", "#92403e", "#e2b7ad"):  # 用户指定莫兰迪色值
        assert token in code, token


def test_scalebar_and_north_arrow():
    code = _code()
    for token in ("annotate", "km_deg", "transAxes"):
        assert token in code, token


def test_landcover_pct_in_metrics():
    assert "landcover_pct" in _code()


def test_workflow_order_clip_then_calibrate():
    """标准工作流: 裁剪 → 像元云掩膜 → 合成 → 定标 (顺序即规范)。"""
    code = _code()
    assert "updateMask" in code and "_mask_cloud" in code
    assert code.index("filterBounds") < code.index("_mask_cloud")
    assert code.index("_mask_cloud") < code.index(".median()")
    assert code.index(".median()") < code.index("multiply(0.0001)")


def test_max_tier_hd_branch():
    """max 挡高清: 服务端 uint8 分类 + 分块拼接 + 沙箱豁免。"""
    code = _code()
    assert 'quality_tier == "max" and not SANDBOX' in code
    for token in ("toByte()", "rows_cap", "np.vstack", "w = 10500"):
        assert token in code, token
    # 分类语义两路同序: 水体(5)在 where 链最后写 = 最优先
    assert code.index(".where(ndvi.gt(0.2), 1)") < \
        code.index(".where(mndwi.gt(0.2), 5)")


def test_gee_adapter_timeout_tier_aware():
    from src.platform import gee_adapter

    assert gee_adapter._EXECUTE_TIMEOUT_S == 240
    assert gee_adapter._EXECUTE_TIMEOUT_MAX_S == 600


def test_generator_contract_workflow_and_aesthetics():
    for token in ("标准工作流", "先裁剪研究区再做校正", "逐像元云掩膜",
                  "莫兰迪", "指北针", "无图例的分类图视为不合格"):
        assert token in GENERATOR_SYSTEM_PROMPT, token


def test_prompt_version_current():
    assert PROMPT_VERSION >= "2.4"
