---
id: ndwi
domain: analysis
type: indices
title: NDWI
aliases:
  - 归一化水体指数
  - Normalized Difference Water Index
  - NDWI
themes: [indices, water]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§水体遥感]"}
verified: true
related: [mndwi, water-extraction, sentinel2]
---

## 定义

NDWI = (Green − NIR) / (Green + NIR)

Sentinel-2 写法: (B3 − B8) / (B3 + B8)。注意与 MNDWI (Green, SWIR)
不是同一个公式, 绝不可混用。

## 适用条件

- 传感器: 需 Green + NIR 波段的光学多光谱; 不适用于 SAR
- 场景: 开阔/清洁水体的提取 (大湖、水库、河流干流); 植被冠层含水量相关分析
- 尺度: 10-30m 分辨率下验证有效

## 判读基准

- NDWI > 0.3 判为开放水体 (非城区, 夏季, Sentinel-2 SR)
- 城区 NDWI 常在 -0.3 ~ +0.3 宽幅波动 (建筑与阴影干扰), 不宜单独判水
- 植被覆盖区 NDVI 与 NDWI 反号是常态, 双指数同看可交叉验证

## 常见错误

- 与 MNDWI 混用波段 (MNDWI 用 SWIR) —— 同名异义陷阱
- 在城市建成区用 NDWI 提水 (建筑阴影大量误分), 应改用 MNDWI
- 对 SAR 影像套用光学指数
- 云未掩膜 (云在高反射波段组合下易呈正值)

## 边界与局限

- 对建筑/道路/阴影敏感, 城市场景优先 MNDWI
- 浑浊或富营养化水体的值域偏移, 固定阈值失效
- 山体阴影误分需地形掩膜兜底

## 关联词条

[[mndwi]], [[water-extraction]], [[sentinel2]]
