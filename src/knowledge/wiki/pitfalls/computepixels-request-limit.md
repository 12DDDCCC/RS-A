---
id: computepixels-request-limit
domain: analysis
type: pitfalls
title: computePixels 请求超 48MB 上限
aliases:
  - GEE请求体积超限
  - Total request size limit
  - EEException 50331648
themes: [pitfalls, gee, export]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
verified: false
related: [change-detection, radiometric-scaling]
---

## 现象

云端执行报错 `EEException: Total request size (50995200 bytes) must be
less than or equal to 50331648 bytes`。实测触发场景: 南京市全域双年份
(2019 vs 2024) 城市扩展变化检测, computePixels 一次拉双期多波段数组,
请求体 ~51MB 超出 48MB (50331648 字节) 硬限。

## 根因

computePixels 的请求体积 = 输出网格像素数 × 波段数 × 单波段字节数。
市域范围 × 10m 原生分辨率 × 双时相 × 多波段组合极易越界; 沙箱试跑用
小区域 mock 通过, 真实 roi 才暴露 —— 属"测试绿生产红"型陷阱。

## 规避方法

- 放大 computePixels 的 scale 参数 (如 10m→30m/60m) 缩小输出网格;
  出图类任务优先按 QUALITY_TIER 目标宽度定网格而非原生分辨率
- 收窄 roi 或分波段多次取结果再本地拼合
- 双时相对比先在服务端 reduce (如分类后差值只回传单波段变化图),
  不把两期原始波段都拉回客户端
- 指标统计用 reduceRegion 服务端聚合, 不经 computePixels

## 关联词条

[[change-detection]], [[radiometric-scaling]]
