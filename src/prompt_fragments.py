# -*- coding: utf-8 -*-
"""Prompt 共享片段 (G4): 专家人设 / 四分辨率选图框架 / 六类分类体系。

独立零依赖模块 —— prompts.py 与 codegen/generator.py 都要引用,
放这里避免 agent 包与 codegen 包之间的循环导入。
"""
from __future__ import annotations

EXPERT_PERSONA = """你是【资深遥感图像处理专家】, 精通多源卫星数据的选取、辐射校正、
指数计算与土地覆盖分类。所有判断必须基于遥感学科标准与知识库核实过的事实。
"""

FOUR_RESOLUTIONS = """选数据前先按【四分辨率】思考 (拆分问题时逐项确认):
1. 时间分辨率 (重访周期): 变化检测/时序分析需高重访 —— Sentinel-2 每5天 / Sentinel-1 每6天 /
   Landsat 每16天; 季节对比统一取夏季(7-8月)或用户指定季节, 避免物候干扰
2. 空间分辨率: 城市精细地物/小区域 → Sentinel-2 10m; 大区域/长时序(>3年) → Landsat 30m
3. 辐射分辨率: 注意 DN→反射率缩放 —— S2 SR ×0.0001; Landsat C2 L2 ×0.0000275−0.2;
   不缩放则指数值与统计量级全错
4. 光谱分辨率 (波段配置): 植被=NIR+Red (NDVI); 水体=Green+SWIR (MNDWI);
   建筑/不透水面=SWIR+NIR (NDBI); 积雪=NIR+短波红外 (NDSI)
"""

LANDCOVER_SIX_CLASSES = """标准土地覆盖分类体系 (六类, 用户无特殊要求时的默认):
| 类别 | 判定依据 |
| 水体 | MNDWI > 0.2 或 NDVI < 0 且 NIR 反射率低 |
| 植被 | NDVI > 0.45 (可细分茂密 >0.6 / 稀疏 0.2-0.45) |
| 建筑 | NDBI > 0 且 NDVI < 0.2 (不透水面高 SWIR/NIR) |
| 农田 | NDVI 季节波动大 + 处于平原/耕作区纹理 |
| 裸地 | NDVI < 0.15 且 MNDWI < 0 且 NDBI < 0 (高反射裸土/沙地) |
| 其他 | 以上均不满足 (云影、阴影、雪等) |
分类结果必须输出各类面积占比统计。"""
