---
id: ndsi
domain: analysis
type: indices
title: NDSI
aliases:
  - 归一化雪指数
  - Normalized Difference Snow Index
  - NDSI
themes: [indices, snow]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§indices_reference]"}
  - {source_id: book-lillesand-rsii, anchor: "[§应用章节 — 雪与冰制图]"}
verified: true
related: [sentinel2]
---

## 定义

NDSI = (Green − SWIR1) / (Green + SWIR1)

Sentinel-2 写法: (B3 − B11) / (B3 + B11); Landsat 写法:
(SR_B3 − SR_B6) / (SR_B3 + SR_B6)。物理基础: 雪在绿光高反射、短波红外低反射。

## 适用条件

- 传感器: 需 Green + SWIR1 波段的光学多光谱; 不适用于 SAR
- 场景: 积雪覆盖制图、雪灾监测、云/雪区分 (配合 SWIR 云检测)
- 尺度: 10-30m 分辨率下验证有效

## 判读基准

- NDSI > 0.4 判为积雪 (冬季晴天影像, 光学 SR, 全球通用经验阈值)
- 0.4 > NDSI > 0 为过渡带 (薄雪/融雪期), 需结合近红外反射率辅助判定
- 水体 NDSI 也可能偏高 (绿光略高于 SWIR), 必须加 NIR 反射率联合判据排除水体

## 常见错误

- 只用 NDSI 单指数判雪, 把湖面/海面误分为雪 (需 NIR>0.11 类联判)
- 与 MNDWI 公式形似而混淆用途 (公式同型但语义不同, 波段对相同判读相反)
- 山体阴影区漏分 (阴影雪 NDSI 下降, 需地形校正或放宽阈值)

## 边界与局限

- 融雪期湿雪反射率整体下降, 固定阈值偏保守
- 林冠遮蔽下的树下雪无法由光学指数探测
- 极高纬度冬季太阳高度角过低, 影像不可用时无解

## 关联词条

[[sentinel2]]
