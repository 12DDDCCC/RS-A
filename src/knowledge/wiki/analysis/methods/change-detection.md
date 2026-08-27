---
id: change-detection
domain: analysis
type: methods
title: 变化检测
aliases:
  - 变化检测
  - Change Detection
  - 后分类比较法 (Post-classification Comparison)
themes: [methods, change]
sources:
  - {source_id: book-zhaoyingshi-analysis, anchor: "[§变化检测]"}
  - {source_id: book-cardille-gee, anchor: "[§Change Detection 部分]"}
  - {source_id: book-meianxin-daolun, anchor: "[§第6章 遥感数字图像计算机分类]"}
verified: true
related: [ndvi, ndbi, landsat9, water-extraction, computepixels-request-limit]
---

## 流程步骤

1. 明确变化定义与类别体系 (什么算"变": 类别转移/强度增减)
2. 选双时相或多时相影像 —— **季节一致**是铁律 (物候差异淹没真实变化);
   长时序优先 Landsat 系 (2013 至今 LC08+LC09 联合), 双时相同季用 S2
3. 每期独立完成: 掩云 → SR 缩放 → 合成 (中值) → 指数计算
4. 选方法族: 代数差值法 (ΔNDVI/ΔNDBI/ΔMNDWI) / 分类后比较 (转移矩阵) /
   时序趋势拟合
5. 阈值判定变化像元 (直方图双峰谷底或分位数), 出转移矩阵/变化图

## 输入输出

- 输入: 两期以上同季节地表反射率影像集 + 行政区 roi + 云量上限
- 输出: 变化二值图或类别转移图 (JPEG) + 各期指标对比 (METRICS)

## 精度参考

- 分类后比较精度 = 两期分类精度的乘积下界 (如各期 85% 则期望 ≤72%),
  结论表述必须携带此衰减意识
- 样区验证: 独立验证点按类别面积分层抽样, 报告混淆矩阵

## 常见失败模式

- 两期影像季节错位仍做指数差值 —— 差出来的是物候不是变化
- 未做辐射一致性 (一期 SR 一期 TOA) 就代数运算
- 把云未掩净的像元判为"剧变"
- 阈值取死常数而不看当期直方图分布
- 市域双时相多波段一次 computePixels 拉回 → 请求超 48MB 硬限
  ([[computepixels-request-limit]]: 放大 scale/服务端先聚合)

## 关联词条

[[ndvi]], [[ndbi]], [[landsat9]], [[water-extraction]]
