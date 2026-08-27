---
source_id: doc-gee-catalog
type: doc
title: "Google Earth Engine 官方数据目录"
authors: ["Google Earth Engine"]
year: 2026
venue: "https://developers.google.com/earth-engine/datasets"
reliability: high
---

# 摘录 — GEE 官方数据目录 (官方文档, reliability=high)

> 本项目已于 2026-08-04 对以下目录页逐字核实并固化进 datasets.json
> (_verified=true)。锚点指向 datasets.json 的核实结论 + 目录页 URL。
> 波段波长/重访/时间覆盖等数值以 datasets.json 为唯一事实镜像,
> 两处不一致 = 构建期错误 (响亮失败)。

## [§COPERNICUS/S2_SR_HARMONIZED] Sentinel-2 L2A 关键参数
原文要点: 地表反射率产品; 2017-03-28 起; 重访 5 天; DN×0.0001 得反射率;
云量属性 CLOUDY_PIXEL_PERCENTAGE。核心波段: B2 蓝 496.6nm/10m、B3 绿
560.0nm/10m、B4 红 664.5nm/10m、B8 近红外 835.1nm/10m、B8A 864.0nm/20m、
B11 SWIR1 1613.7nm/20m、B12 SWIR2 2202.4nm/20m; 掩膜波段 SCL(20m)/QA60(60m)。
> 关键句: 目录页 key_indices 给出 NDVI=(B8−B4)/(B8+B4)、
> NDWI=(B3−B8)/(B3+B8)、MNDWI=(B3−B11)/(B3+B11)。

## [§COPERNICUS/S2_HARMONIZED] Sentinel-2 L1C TOA
原文要点: 2015 年 6 月起可用; 含 B10 卷云波段(SR 版无); 新代码统一用
HARMONIZED 集合消除 2022 baseline 04.00 处理偏移。
> 关键句: TOA 未做大气校正, 与 SR 不可混用于同一时序。

## [§LANDSAT/LC09/C02/T1_L2] Landsat 9 C2 L2 关键参数
原文要点: 2021-10 起可用; 30m; 重访 16 天; SR = DN×2.75e-05 − 0.2;
云量属性 CLOUD_COVER; 核心波段 SR_B2 蓝(0.45-0.51μm)、SR_B3 绿
(0.53-0.59μm)、SR_B4 红(0.64-0.67μm)、SR_B5 近红外(0.85-0.88μm)、
SR_B6 SWIR1(1.57-1.65μm); LC08 同编号波段自 2013 年起可复用。
> 关键句: 目录页 key_indices 给出 NDVI=(SR_B5−SR_B4)/(SR_B5+SR_B4)、
> MNDWI=(SR_B3−SR_B6)/(SR_B3+SR_B6)。

## [§COPERNICUS/S1_GRD] Sentinel-1 SAR GRD 关键参数
原文要点: 2014-10-03 起可用; IW 模式 10m; VV/VH 双极化, 单位 dB,
无辐射缩放系数; 不受云雨影响全天候成像。
> 关键句: 水体在 SAR 后向散射图像上呈暗色, 提取用 VV/VH 阈值法且无全局
> 魔法阈值, 必须用 Otsu 自适应。

## [§cloud_field_warning] 云量字段不可跨数据集互换
原文要点: Sentinel-2 用 CLOUDY_PIXEL_PERCENTAGE, Landsat 用 CLOUD_COVER;
在 Landsat 上误用 S2 字段名会静默失败(字段不存在不报错)。
> 关键句: filterMetadata 用错字段名 = 空集合静默通过, 是隐性 bug 源。

## [§indices_reference] 同名异义陷阱 (NDWI vs MNDWI)
原文要点: NDWI 用 Green+NIR (McFeeters 定义), MNDWI 用 Green+SWIR;
两者公式不同用途不同, 城市区混用会大量误分建筑/阴影为水体。
NDBI=(SWIR1−NIR)/(SWIR1+NIR) 高值对应建成区 (学界标准定义);
NDSI=(Green−SWIR1)/(Green+SWIR1), 雪在绿光高反射、短波红外低反射。
> 关键句: 归一化指数的分子分母顺序是语义本体, 反向即换义。
