---
source_id: book-cardille-gee
type: book
title: "Cloud-Based Remote Sensing with Google Earth Engine: Fundamentals and Applications"
authors: ["Jeffrey A. Cardille", "Morgan A. Crowley", "David Saah", "Nicholas E. Clinton"]
year: 2024
venue: "Springer (官方免费电子书 eebook.org)"
reliability: high
---

# 摘录 — Cloud-Based Remote Sensing with GEE (Cardille et al., 2024)

> 官方免费出版的高可信教材 (reliability=high)。锚点用部/篇级主题,
> 章节编号体系(F/A/B/C/D 开头)未逐章核对前不写具体编号。

## [§Fundamentals 部分] 云计算范式与 scale/projection
原文要点: GEE 中所有计算声明式地作用于 ImageCollection, 计算发生在
Google 服务端; 每个影像自带原生分辨率(scale)与投影, 显式指定错误的
scale 会改变统计的像元聚合粒度。
> 关键句: 在 GEE 里"分辨率"是计算的输入参数, 不是重采样的结果。

## [§Image Collection 部分] 合成(compositing)与去云
原文要点: 时间序列合成(中值合成等)用于消除云污染与轨道重叠;
云掩膜应基于 QA 波段或云分数在合成之前逐景应用。
> 关键句: 先掩云后合成, 顺序颠倒会把云边混入中值。

## [§Classification 部分] 监督分类工作流
原文要点: 监督分类流程 = 采样点采集 → 划分训练/验证集 → 训练分类器
(如 CART/随机森林) → 分类 → 用验证集算混淆矩阵评估精度。
> 关键句: 验证样本必须与训练样本空间独立, 否则精度虚高。

## [§Change Detection 部分] 变化检测实现要点
原文要点: 云平台上的变化检测常采用双时相指数差值、时序趋势拟合
(如 LandTrendr) 与分类后对比三类; 双时相法须保证两期影像季节一致。
> 关键句: 季节错位的两期影像做差值, 物候差异会淹没真实变化。

## [§Applications 部分] 应用案例域
原文要点: 全书应用篇覆盖水体制图、土地覆盖、城市扩张、农业、灾害等
领域, 各案例均强调"数据集选择→掩膜→指标→阈值→验证"的可复现链条。
> 关键句: 应用案例的价值在于完整的可复现参数链, 不止是结果图。

## [§Data 部分] 目录元数据的用法
原文要点: 每个数据集目录页标注 provider/temporal availability/分辨率/
波段表/缩放系数与云量属性名, 是编写代码前必须核实的权威事实源。
> 关键句: 数据集 ID 与波段名一律以目录页为准, 凭记忆写必错。
