---
id: radiometric-scaling
domain: image-processing
type: methods
title: 辐射定标与反射率缩放
aliases:
  - 辐射定标
  - Radiometric Scaling and Calibration
  - DN转反射率
themes: [preprocessing, calibration]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: doc-gee-catalog, anchor: "[§LANDSAT/LC09/C02/T1_L2]"}
  - {source_id: book-meianxin-daolun, anchor: "[§第4章 遥感图像处理]"}
verified: true
related: [reflectance-forms, sentinel2, landsat9, computepixels-request-limit]
---

## 流程步骤

1. 查数据集目录页确认缩放方式 (逐字核实, 不凭记忆)
2. Sentinel-2 SR (L2A): 线性缩放 `DN × 0.0001` → 地表反射率
3. Landsat C2 L2: 仿射缩放 `SR = DN × 0.0000275 − 0.2` (USGS 官方系数)
4. 缩放后再计算指数/统计; 输出指标保留 3-4 位小数

## 输入输出

- 输入: 原始 DN 波段 + 数据集对应的 scale factor
- 输出: 物理量纲的反射率波段 (值域约 [-1, 1], 有效地表通常 0-1)

## 精度参考

- 缩放系数来自官方目录页, 属确定性变换无精度损失; 错用系数是系统性错误
- Sentinel-1 GRD 为 dB 后向散射, **无**辐射缩放系数 (scale_factor=null)

## 常见失败模式

- Landsat 忘减 offset (-0.2), 全图反射率整体偏高 ~0.2, NDVI 系统性虚高
- 把 S2 的 ×0.0001 用到 Landsat 上 (跨产品抄系数)
- 对 S1 找缩放系数 (不存在, 单位是 dB)
- GEE 中显式指定错误 scale 参数改变统计聚合粒度 (分辨率是计算输入,
  不是重采样结果)

## 关联词条

[[reflectance-forms]], [[sentinel2]], [[landsat9]]
