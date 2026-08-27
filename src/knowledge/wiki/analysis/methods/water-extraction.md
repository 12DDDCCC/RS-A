---
id: water-extraction
domain: analysis
type: methods
title: 水体提取
aliases:
  - 水体提取
  - Water Body Extraction
  - Surface Water Mapping
themes: [methods, water]
sources:
  - {source_id: doc-gee-catalog, anchor: "[§COPERNICUS/S1_GRD]"}
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§水体遥感]"}
  - {source_id: book-cardille-gee, anchor: "[§Applications 部分]"}
verified: true
related: [mndwi, ndwi, sentinel1, change-detection, cloud-masking]
template_hint: land_cover_v1
---

## 流程步骤

1. 选数据: 城区/建筑密集区用光学 MNDWI; 云污染期/洪水应急用 SAR (Sentinel-1)
2. 光学路线: 掩云 (SCL/QA60) → SR 缩放 → 计算 MNDWI → 阈值分割
3. SAR 路线: 取 VV 或 VH 波段 → 斑点噪声滤波 → **Otsu 自适应阈值**二值化
4. 后处理: 剔除小图斑 (面积过滤), 山区叠加坡度掩膜排除山体阴影
5. 统计水体面积/占比出图

## 输入输出

- 输入: 影像集 (S2_SR 或 S1_GRD) + roi + 时相; 湿地/洪道类目标建议
  春秋两期对比 (季相波动大)
- 输出: 水体二值图 (JPEG) + 水体面积与占比指标 (METRICS)

## 精度参考

- 清洁开阔水体 MNDWI 法在非城区精度通常 >90% (教材级经验值,
  实际以混淆矩阵为准)
- SAR Otsu 法对城市区洪水淹没制图是行业标准做法, 但平滑水面风致
  粗糙度升高会漏分

## 常见失败模式

- 固定阈值跨季节跨区域套用 (浑浊水/汛期值域漂移)
- 城市区用 NDWI 而不是 MNDWI —— 建筑阴影大量误分
- SAR 未滤波直接 Otsu (斑点噪声破坏直方图双峰形态)
- 山体阴影当水体 (缺坡度掩膜兜底)
- 云未掩净, 云影混入水体统计

## 关联词条

[[mndwi]], [[ndwi]], [[sentinel1]], [[change-detection]]
