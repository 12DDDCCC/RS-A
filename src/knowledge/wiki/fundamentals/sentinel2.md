---
id: sentinel2
domain: fundamentals
type: satellites
title: Sentinel-2
aliases:
  - 哨兵二号
  - Sentinel-2
  - S2
themes: [satellite, optical]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: book-cardille-gee, anchor: "[§Data 部分]"}
verified: true
related: [landsat9, ndvi, mndwi, ndwi, ndsi, cloud-masking, radiometric-scaling]
template_hint: land_cover_v1
---

## 波段表

| 编号 | 中心波长 | 分辨率 |
|---|---|---|
| B2 | 496.6nm (蓝) | 10m |
| B3 | 560.0nm (绿) | 10m |
| B4 | 664.5nm (红) | 10m |
| B8 | 835.1nm (近红外) | 10m |
| B8A | 864.0nm (窄近红外) | 20m |
| B11 | 1613.7nm (短波红外1) | 20m |
| B12 | 2202.4nm (短波红外2) | 20m |

掩膜波段: SCL (场景分类, 20m)、QA60 (云掩膜, 60m)。L2A 无 B10 卷云波段。

## 重访周期

双星 (A/B) 联合重访 5 天; 单星 10 天。2017-03-28 起 L2A 覆盖可用
(早期年份覆盖非全球); L1C 自 2015-06 起可用。新代码统一用 HARMONIZED 集合。

## 数据集ID

- 地表反射率 (SR, 默认首选): `COPERNICUS/S2_SR_HARMONIZED`
- 大气顶反射率 (TOA): `COPERNICUS/S2_HARMONIZED`
- 缩放: DN × 0.0001 得反射率; 云量属性 `CLOUDY_PIXEL_PERCENTAGE`
