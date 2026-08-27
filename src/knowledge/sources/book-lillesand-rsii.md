---
source_id: book-lillesand-rsii
type: book
title: "Remote Sensing and Image Interpretation (7th Edition)"
authors: ["Thomas M. Lillesand", "Ralph W. Kiefer", "Jonathan W. Chipman"]
year: 2015
venue: "Wiley"
reliability: medium
---

# 摘录 — Remote Sensing and Image Interpretation, 7th ed (Lillesand, Kiefer & Chipman, 2015)

> 教材级知识源 (reliability=medium)。锚点用章级 [§ChN 短标题],
> 不标具体页码 —— 页码未逐页核对前不写, 防锚点幻觉。

## [§Ch1 Concepts and Foundations] 遥感过程七要素
原文要点: 遥感过程包括能量源(a)、大气传播(b)、与目标交互(c)、
传感器记录(d)、地面接收(e)、图像处理(f)、解译与应用(g)。
> 关键句: 每一环节都会引入误差, 解译结论必须考虑链路全程的退化。

## [§Ch1 Concepts and Foundations] 大气散射三类型
原文要点: 瑞利散射强度与波长的四次方成反比, 主要影响可见光短波段(蓝光);
米氏散射由粒径接近波长的气溶胶引起; 无选择性散射(云雾水滴, 粒径远大于
波长)对各波长同等散射, 故云呈白色。
> 关键句: 蓝波段受瑞利散射污染最重 —— 深蓝/蓝波段常用于气溶胶校正而非地物分析。

## [§Ch1 Concepts and Foundations] 大气窗口与传感器波段设计
原文要点: 传感器工作波段必须位于大气透过率高的窗口内; 常用窗口包括
可见光-近红外、中红外(1.5-1.8 / 2.0-3.5μm)、热红外(8-14μm)与微波全窗口。
> 关键句: 微波几乎不受云雨衰减, 是全天候成像的物理基础。

## [§Ch1 Concepts and Foundations] 反射率形态与被动光学前提
原文要点: 被动光学遥感记录的是太阳辐照经地表反射后的能量, 太阳同步轨道
卫星在当地时间上午10点半左右过境以保证足够太阳高度角。
> 关键句: 表观反射率(top of atmosphere)含大气贡献, 与地表反射率不可混用。

## [§卫星平台章节 (Earth Resource Satellites)] 卫星轨道与重访
原文要点: 太阳同步近极地轨道保证每次过境光照条件近似一致;
重访周期取决于轨道设计、幅宽(swath width)与侧摆能力,
幅宽越宽重访越快但分辨率往往越粗。
> 关键句: 重访周期是轨道+幅宽+侧摆三者共同决定的系统参数。

## [§数字图像处理章节 (Digital Image Analysis)] 辐射校正层次
原文要点: 从 DN 到可比反射率的链条为: 辐射定标(radiometric calibration)
→ 大气顶辐亮度 → 表观反射率 → 大气校正后的地表反射率;
几何上需做系统几何校正与正射纠正才能叠加分析。
> 关键句: 多时相代数运算前必须完成辐射与几何两套归一。

## [§应用章节 — 雪与冰制图] 雪的波谱特征
原文要点: 雪在可见光波段反射率很高而在短波红外急剧下降, 这一"绿光高/
SWIR低"对比是归一化雪指数(NDSI)识别雪的物理基础; 云在 SWIR 的表现与雪不同,
可用于云雪区分。
> 关键句: NDSI = (Green − SWIR1) / (Green + SWIR1), 高值指示雪面。
