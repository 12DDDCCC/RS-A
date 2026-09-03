# -*- coding: utf-8 -*-
"""O1 模板管线测试: 参数提取 / 渲染产物过防护 / 多年份回退。"""
from __future__ import annotations

from src.codegen.templates import extract_params, render, try_template


def test_extract_single_year():
    params = extract_params("land_cover", "南京市2024年7-8月土地覆盖 云量25%")
    assert params is not None
    assert params.years == ["2024"] and params.cloud == 25
    assert params.template_id == "land_cover_v1"


def test_extract_default_cloud():
    p = extract_params("vegetation", "北京2023年植被状况")
    assert p is not None and p.cloud == 30 and p.years == ["2023"]


def test_multi_year_not_matched():
    # 显式端点对 (2021 vs 2025) 现在命中多期模板 (区间逐期展开, 见下)
    assert extract_params("change_detection", "2023年变化") is None  # 单年变化无意义
    assert extract_params("vegetation", "对比2021年与2025年覆盖") is None  # vegetation 只单期
    # 跨度 >6 期交给编排层拆分
    assert extract_params("land_cover", "南京市2014-2024十年土地利用变化") is None


def test_endpoint_pair_expands_to_span():
    """clarify 物化『近5年』成端点对 (2020,2024): 区间逐期展开命中多期模板。"""
    p = extract_params("land_cover",
                       "南京市2020-2024年土地利用状况时序变化检测与分类")
    assert p is not None
    assert p.template_id == "landcover_change_v1"
    assert p.years == ["2020", "2021", "2022", "2023", "2024"]
    p2 = extract_params("land_cover", "对比2021年与2025年覆盖")
    assert p2 is not None and p2.years == ["2021", "2022", "2023", "2024", "2025"]


def test_unknown_type_not_matched():
    assert extract_params("snow", "2024年积雪") is None


def test_recent_years_expansion():
    """『近N年』多期展开: change_detection/land_cover 无显式年份时命中。"""
    from datetime import date
    p = extract_params("change_detection", "研究南京市近5年的土地利用变化情况")
    assert p is not None and p.template_id == "landcover_change_v1"
    # 锚定 today 使断言稳定: 2026-09 起近5年 = 2022..2026
    from src.codegen.templates import _recent_years
    ys = _recent_years("近5年", today=date(2026, 9, 1))
    assert ys == ["2022", "2023", "2024", "2025", "2026"]
    # 未到 9 月回退一年 (夏季窗口不完整)
    assert _recent_years("近5年", today=date(2026, 3, 1)) == \
        ["2021", "2022", "2023", "2024", "2025"]
    # 中文数字 / 超上限 / 近1年 均不命中
    assert _recent_years("最近五年变化", today=date(2026, 9, 1)) == ys
    assert _recent_years("近10年变迁", today=date(2026, 9, 1)) is None
    assert _recent_years("近1年", today=date(2026, 9, 1)) is None


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


def test_rendered_multiyear_passes_protections():
    """多期模板渲染产物同样过白名单 + 学科校验, 且沙箱只跑第一期。"""
    hit = try_template("change_detection", "南京市近5年土地利用变化",
                       "南京市近5年土地利用变化")
    assert hit is not None
    code, params = hit
    assert params.template_id == "landcover_change_v1"
    assert "years = years[:1]" in code  # 沙箱截断
    from src.codegen.validator import validate_code
    from src.codegen.domain_validator import verify_domain

    v = validate_code(code)
    assert v.passed, v.issues
    d = verify_domain(code)
    assert d.passed, d.issues
    # 多期核心输出: 各年指标 + 首末变化统计
    assert 'METRICS["years"]' in code and "landcover_change_pct" in code


def test_render_uses_no_llm_markers():
    code = render(extract_params("water", "太湖2024年水体提取"))
    assert "OUTPUT_JPEG" in code and "METRICS" in code
    assert "SANDBOX" in code
    # 2.3 起分类图走 ListedColormap+Patch 图例 (正规成果三要素), 不再是连续色带
    assert "ListedColormap" in code and "plt.legend(" in code and "Patch(" in code
