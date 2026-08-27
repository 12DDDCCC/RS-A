---
id: mndwi
domain: analysis
type: indices
title: MNDWI
aliases:
  - 改进归一化差异水体指数
  - Modified Normalized Difference Water Index
  - MNDWI
themes: [indices, water]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§水体遥感]"}
verified: true
related: [ndwi, water-extraction, sentinel2, landsat9]
template_hint: land_cover_v1
---

## 定义

MNDWI = (Green − SWIR1) / (Green + SWIR1)

Sentinel-2 写法: (B3 − B11) / (B3 + B11); Landsat 9 C2 L2 写法:
(SR_B3 − SR_B6) / (SR_B3 + SR_B6)。注意与 NDWI (Green, NIR) 不是同一个公式。

## 适用条件

- 传感器: 需 Green + SWIR 波段的光学多光谱; 不适用于 SAR
- 场景: 城市/建成区及其周边的水体提取优于 NDWI (SWIR 对建筑高反射,
  抑制建筑背景噪音)
- 尺度: 10-30m 分辨率下验证有效

## 判读基准

- MNDWI > 0.2 判为水体 (城区, 夏季, Sentinel-2 SR)
- 0 < MNDWI ≤ 0.2 为过渡带, 需结合直方图谷底或辅助掩膜判定
- 大面积陆地均值常为负 (-0.1 ~ -0.3), 属正常形态非异常
- 高浑浊水体阈值需下调至 0 附近 (汛期/含沙量大时)

## 常见错误

- 与 NDWI 混用波段 (NDWI 用 NIR, MNDWI 用 SWIR) —— 同名异义陷阱
- 对 SAR 影像套用光学指数
- 把云/冰雪当水体 (二者在 SWIR 也低反射, 必须先掩云)
- 固定阈值跨季节跨区域套用

## 边界与局限

- 云、冰雪会干扰; 山体阴影在 SWIR 低反射易误判为水体 (需坡度掩膜兜底)
- 潮间带/浅滩因水深与浑浊度变化, 阈值不稳定
- 需地表反射率数据; TOA 直接计算会引入大气偏差

## 关联词条

[[ndwi]], [[water-extraction]], [[sentinel2]], [[landsat9]]
