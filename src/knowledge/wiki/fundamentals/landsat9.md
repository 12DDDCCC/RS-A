---
id: landsat9
domain: fundamentals
type: satellites
title: Landsat 9
aliases:
  - 陆地卫星九号
  - Landsat 9
  - LC09
themes: [satellite, optical]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§LANDSAT/LC09/C02/T1_L2]"}
  - {source_id: book-cardille-gee, anchor: "[§Data 部分]"}
verified: true
related: [sentinel2, ndvi, ndbi, change-detection, mndwi, cloud-masking, radiometric-scaling]
template_hint: land_cover_v1
---

## 波段表

| 编号 | 波长 | 分辨率 |
|---|---|---|
| SR_B2 | 0.45-0.51μm (蓝) | 30m |
| SR_B3 | 0.53-0.59μm (绿) | 30m |
| SR_B4 | 0.64-0.67μm (红) | 30m |
| SR_B5 | 0.85-0.88μm (近红外) | 30m |
| SR_B6 | 1.57-1.65μm (短波红外1) | 30m |

Landsat 8 (LC08) 波段编号相同可复用; Landsat 7 编号不同, 跨传感器不可复用。

## 重访周期

16 天单星; 与 Landsat 8 错相 8 天联合重访。LC09 自 2021-10 起可用,
LC08 自 2013 年起可用 —— 2013 至今长时序必须 LC08+LC09 联合。

## 数据集ID

- 地表反射率: `LANDSAT/LC09/C02/T1_L2` (LC08 用 `LANDSAT/LC08/C02/T1_L2`)
- 缩放: SR = DN × 2.75e-05 − 0.2 (仿射, USGS 官方)
- 云量属性 `CLOUD_COVER` (与 Sentinel-2 的字段名不可互换)
