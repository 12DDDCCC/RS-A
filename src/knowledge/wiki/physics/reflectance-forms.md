---
id: reflectance-forms
domain: physics
type: concept
title: 反射率三形态
aliases:
  - 反射率三种形态
  - Surface Reflectance vs TOA Reflectance
  - 地表反射率
themes: [physics, reflectance]
sources:
  - {source_id: book-lillesand-rsii, anchor: "[§Ch1 Concepts and Foundations]"}
  - {source_id: book-meianxin-daolun, anchor: "[§第4章 遥感图像处理]"}
verified: true
related: [atmospheric-window, radiometric-scaling]
---

## 定义

同一像元的"反射率"有三种形态, 定量含义完全不同:

1. **DN 值**: 传感器原始量化输出, 无物理量纲, 不可比较
2. **表观反射率 (TOA, top-of-atmosphere)**: 辐照度定标后含大气贡献的
   反射率, 同一平台内可比
3. **地表反射率 (SR, surface reflectance)**: 经大气校正后的真实地表反射,
   跨时相/跨传感器定量分析的唯一合法形态

## 适用条件

- 场景: 单景看图可用 TOA; 指数计算、时序对比、阈值判读必须 SR
- Sentinel-2 L2A 与 Landsat C2 L2 都是 SR 产品, 直接缩放 DN 即得

## 判读基准

- 数据集名含 L2A / T1_L2 → SR 形态 (直接可用)
- 数据集为 L1C / TOA → 表观形态 (只做定性或单景相对分析)
- 两期影像一期 SR 一期 TOA 做差值 = 方法错误 (辐射基准不同)

## 常见错误

- 把 DN 当反射率直接算指数 (值域完全错乱)
- TOA 与 SR 跨产品混用时序而不声明
- 认为大气校正可以事后用阈值弥补

## 边界与局限

- SR 产品自身的大气校正精度受气溶胶模型限制, 高浑浊/高纬度区质量下降
- 云区无有效反射率可言, 必须先掩膜

## 关联词条

[[atmospheric-window]], [[radiometric-scaling]]
