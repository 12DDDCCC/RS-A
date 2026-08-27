# -*- coding: utf-8 -*-
"""O1 模板管线测试: 参数提取 / 渲染产物过防护 / 多年份回退。"""
from __future__ import annotations

from src.codegen.templates import extract_params, render, try_template


def test_extract_single_year():
    params = extract_params("land_cover", "南京市2024年7-8月土地覆盖 云量25%")
    assert params is not None
    assert params.year == "2024" and params.cloud == 25
    assert params.template_id == "land_cover_v1"


def test_extract_default_cloud():
    p = extract_params("vegetation", "北京2023年植被状况")
    assert p is not None and p.cloud == 30 and p.year == "2023"


def test_multi_year_not_matched():
    assert extract_params("land_cover", "对比2021年与2025年覆盖") is None
    assert extract_params("change_detection", "2023年变化") is None


def test_unknown_type_not_matched():
    assert extract_params("snow", "2024年积雪") is None


def test_rendered_code_passes_protections():
    hit = try_template("land_cover", "南京市2024年土地覆盖分析", "云量30%")
    assert hit is not None
    code, _ = hit
    from src.codegen.validator import validate_code
    from src.codegen.domain_validator import verify_domain

    v = validate_code(code)
    assert v.passed, v.issues
    d = verify_domain(code)
    assert d.passed, d.issues


def test_render_uses_no_llm_markers():
    code = render(extract_params("water", "太湖2024年水体提取"))
    assert "OUTPUT_JPEG" in code and "METRICS" in code
    assert "SANDBOX" in code
    # 2.3 起分类图走 ListedColormap+Patch 图例 (正规成果三要素), 不再是连续色带
    assert "ListedColormap" in code and "plt.legend(" in code and "Patch(" in code
