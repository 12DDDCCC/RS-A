---
id: ndbi
domain: analysis
type: indices
title: NDBI
aliases:
  - 归一化建筑指数
  - Normalized Difference Built-up Index
  - NDBI
themes: [indices, urban]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§indices_reference]"}
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§城市遥感]"}
verified: true
related: [ndvi, landsat9, change-detection]
template_hint: land_cover_v1
---

## 定义

NDBI = (SWIR1 − NIR) / (SWIR1 + NIR)

Landsat 9 C2 L2 写法: normalizedDifference(['SR_B6', 'SR_B5']);
Sentinel-2 写法: normalizedDifference(['B11', 'B8'])。分子是 SWIR1 − NIR,
方向与 NDVI 相反。

## 适用条件

- 传感器: 需 NIR + SWIR1 波段的光学多光谱; 不适用于 SAR
- 场景: 建成区/不透水面识别、城市扩展监测; 常与 NDVI/MNDWI 组合做土地覆盖
- 尺度: 10-30m 分辨率下验证有效

## 判读基准

- NDBI > 0 判为建成/裸露区候选 (生长季影像, 光学 SR, 与 NDVI<0.4 联合判定)
- 植被覆盖区 NDBI 常为负 (-0.3 ~ -0.5), 属正常形态非异常
- 城市扩展分析常用 ΔNDBI = NDBI(后期) − NDBI(前期) > 0 标记扩张像元

## 常见错误

- 分子写反 (写成 NIR − SWIR1 则变成"反建筑指数", 语义全反)
- 把裸土/沙地当建成区 (二者在 SWIR/NIR 对比上同型, 需辅助数据区分)
- 单独用 NDBI 下结论而不与 NDVI 交叉验证

## 边界与局限

- 裸土与建筑混叠是该指数固有混淆源, 干旱区慎用
- 云/雪在 SWIR 高反射会产生假阳性, 必须先掩膜
- 需地表反射率; TOA 直接计算跨期不可比

## 关联词条

[[ndvi]], [[landsat9]], [[change-detection]]
