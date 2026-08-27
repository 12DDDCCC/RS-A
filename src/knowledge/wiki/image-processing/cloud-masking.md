---
id: cloud-masking
domain: image-processing
type: methods
title: 云掩膜
aliases:
  - 云掩膜
  - 去云
  - Cloud Masking
themes: [preprocessing, clouds]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S2_SR_HARMONIZED]"}
  - {source_id: book-cardille-gee, anchor: "[§Image Collection 部分]"}
verified: true
related: [sentinel2, landsat9, water-extraction]
---

## 流程步骤

1. Sentinel-2 SR: 优先用 SCL 波段 (场景分类, 20m) —— 剔除 SCL∈{3(云影),
   8(云中概率),9(云高概率),10(卷云)}; QA60 作备用 (60m, 粒度粗)
2. Landsat C2 L2: 用 `CLOUD_COVER` 元数据先筛低云量景 (集合级 filter),
   像元级用 QA_PIXEL 云位判断
3. 掩膜必须在合成/指数计算**之前**逐景应用 (先掩云后合成)
4. 时序分析配合时间合成 (中值) 消除残余薄云

## 输入输出

- 输入: 影像集 (含 QA/SCL 波段) + 云量阈值
- 输出: 无云像元集合 (掩膜后的 ImageCollection)

## 精度参考

- SCL 对厚云识别可靠; 薄云/云影漏检是主要误差源 (教材级经验,
  以目视抽检为准)
- 云量属性筛选: Sentinel-2 用 `CLOUDY_PIXEL_PERCENTAGE`,
  Landsat 用 `CLOUD_COVER` —— 字段名不可互换 (错用静默失败)

## 常见失败模式

- 先合成后掩云 (云边混入中值)
- 在 Landsat 上用 S2 的云量字段名 → 空集合静默通过
- 云影未剔除 (SCL 类别 3), 云影被误判为水体
- 单景无可用像元时仍出图 (应报"该时段云量过高"而非硬算)

## 关联词条

[[sentinel2]], [[landsat9]], [[water-extraction]]
