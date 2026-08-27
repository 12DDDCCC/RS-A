---
id: sentinel1
domain: fundamentals
type: satellites
title: Sentinel-1
aliases:
  - 哨兵一号
  - Sentinel-1
  - S1
themes: [satellite, sar]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S1_GRD]"}
  - {source_id: book-lillesand-rsii, anchor: "[§Ch1 Concepts and Foundations]"}
verified: true
related: [water-extraction]
---

## 波段表

| 编号 | 说明 | 分辨率 |
|---|---|---|
| VV | 垂直发射垂直接收 (主极化) | IW 模式 10m |
| VH | 垂直发射水平接收 (交叉极化) | IW 模式 10m |

C 波段 SAR, 单位 dB, 无辐射缩放系数; 不受云雨影响全天候成像。

## 重访周期

6 天 (双星联合); 2014-10-03 起可用。洪水等应急监测的首选数据源。

## 数据集ID

- 地面检测多视产品: `COPERNICUS/S1_GRD`
- 水体提取用 VV/VH 的 Otsu 自适应阈值法; 无全局魔法阈值
