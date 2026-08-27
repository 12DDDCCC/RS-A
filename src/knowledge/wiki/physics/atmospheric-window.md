---
id: atmospheric-window
domain: physics
type: concept
title: 大气窗口
aliases:
  - 大气窗口
  - Atmospheric Window
  - atmospheric window
themes: [physics, atmosphere]
sources:
  - {source_id: book-meianxin-daolun, anchor: "[§第2章 电磁波与地物波谱特性]"}
  - {source_id: book-lillesand-rsii, anchor: "[§Ch1 Concepts and Foundations]"}
verified: true
related: [reflectance-forms]
---

## 定义

大气对电磁波吸收较弱、透过率较高的波长区间称为大气窗口 (Atmospheric
Window)。遥感传感器的工作波段必须落在窗口内, 否则地表信息在到达传感器
之前已被大气吸收殆尽。

## 适用条件

- 被动光学遥感 (可见光-近红外-短波红外) 依赖太阳辐射经"大气两次穿透"
  (下行+上行), 窗口外波段不可用
- 微波 (雷达) 波长长, 几乎不受云雨衰减, 近似全天候窗口

## 判读基准

- 可见光-近红外 0.4-1.3μm: 植被/水体/土地覆盖分析主窗口
- 短波红外 1.5-1.8μm 与 2.0-3.5μm: SWIR 波段所在 (MNDWI/NDBI/NDSI 依赖)
- 热红外 8-14μm: 地表温度反演窗口
- 云在可见光窗口不透明 —— 光学数据云像元无效, 需掩膜或换 SAR

## 常见错误

- 认为任何波段都能"拍到"地表信息 (窗口外的波段设计不存在于业务卫星)
- 忽视瑞利散射对蓝光段的污染 (蓝波段常用于气溶胶而非地物判读)

## 边界与局限

- 窗口内仍有部分吸收与散射残留, 表观反射率含大气贡献, 定量比较须校正
- 云覆盖是光学窗口的根本性限制, 不是算法能完全弥补的

## 关联词条

[[reflectance-forms]]
