---
id: ndvi
domain: analysis
type: indices
title: NDVI
aliases:
  - 归一化植被指数
  - Normalized Difference Vegetation Index
  - NDVI
themes: [indices, vegetation]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§植被遥感]"}
verified: true
related: [ndbi, sentinel2, landsat9, change-detection]
template_hint: land_cover_v1
---

## 定义

NDVI = (NIR − Red) / (NIR + Red)

Sentinel-2 写法: (B8 − B4) / (B8 + B4); Landsat 9 C2 L2 写法:
(SR_B5 − SR_B4) / (SR_B5 + SR_B4)。分子必须是 NIR − Red, 不可反向
(反向后植被全为负值)。

## 适用条件

- 传感器: 需 Red + NIR 波段的光学多光谱; 不适用于 SAR
- 场景: 植被长势/覆盖度/物候监测的默认首选指数
- 尺度: 10-30m 分辨率下验证充分; 粗分辨率受混合像元影响需谨慎

## 判读基准

- NDVI > 0.4 判为有植被覆盖 (生长季, 光学 SR 数据, 中纬度地区)
- 0 < NDVI ≤ 0.1 多为裸土/建筑/稀疏植被 (非生长季或干旱区, 属正常形态)
- NDVI < 0 通常指示水体或云影 (夏季晴天影像, 需先掩云再判读)
- 高覆盖林区生长季常达 0.7-0.9, 但 >0.85 后趋于饱和, 绝对值不再可靠区分生物量

## 常见错误

- 分子写反成 (Red − NIR), 植被呈负值还误判为水体
- 用 TOA 反射率直接跨年比较而不做一致性处理
- 对 SAR 影像套用光学指数
- 忘记除零保护 (云/水面像元 NIR+Red 可能接近 0)

## 边界与局限

- 高生物量区饱和, 对茂密森林的差异不敏感 (改用 EVI 类)
- 土壤背景拉低低覆盖区数值; 大气与气溶胶对红波段影响大
- 需地表反射率 (SR); 云污染区必须先掩膜再统计

## 关联词条

[[ndbi]], [[sentinel2]], [[landsat9]], [[change-detection]]
