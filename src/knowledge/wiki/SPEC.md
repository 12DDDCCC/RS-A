# 遥感知识库 Wiki 规范 (Wiki SPEC v1.1)

> 本文件是 `src/knowledge/wiki/` 知识库的**唯一权威规范**。
> 任何词条的新增/修改/引用都必须遵守本规范; 不合规范的词条视为不存在。
> 修改本规范 = PROMPT_VERSION 之外单独递增 WIKI_SPEC_VERSION (文末)。

---

## 1. 目的与设计原则

为 agent 提供**可审计、零幻觉、带出处**的遥感领域事实知识。三条铁律:

1. **无出处不入库**: 每条事实必须能追溯到具体书籍页码或论文; "网上说的"不算
2. **精确索引不模糊召回**: 词条只经 index 三层路由命中, 不做语义近似
3. **阈值必须带条件**: 遥感没有无条件阈值 —— "MNDWI>0.2 为水体"必须写明
   (传感器/季节/区域背景), 无条件阈值是幻觉之源

## 2. 目录布局 (三段流水线 × 五大主题域)

### 2.1 五大主题域 (提取工作的顶层划分, 按遥感学科经典分层)

提取者按 D1→D5 顺序从书籍/论文中提取知识; 每个域的"应提取清单"见 §2.2。

| 域 | 目录 | 学科对应 | 回答的问题 |
|---|---|---|---|
| **D1 遥感基础知识** | `fundamentals/` | 绪论/系统组成 | 遥感是什么, 数据长什么样 |
| **D2 遥感物理支撑** | `physics/` | 电磁波与辐射传输 | 为什么能遥感到, 量怎么算 |
| **D3 遥感基本图像处理** | `image-processing/` | 数字图像处理 | 原始数据怎么变成可用影像 |
| **D4 遥感分析方式** | `analysis/` | 指数/分类/变化检测 | 怎么从影像得到结论 |
| **D5 遥感应用** | `applications/` | 行业应用 | 各领域怎么做、阈值是什么 |

横切补充: `pitfalls/` (反模式库) 不属于单一域 —— 五域提取中发现的
翻车模式都归入此目录; 卫星/传感器词条按其数据特性归入 D1 或 D3。

### 2.2 各域应提取信息清单

**D1 fundamentals — 提取**: 遥感定义与广义/狭义之分; 遥感系统五大组成
(信息源/传输/接收/处理/应用); 分类体系 (主动vs被动、航天vs航空vs地面、
成像vs非成像); 分辨率四要素 (空间/光谱/辐射/时间, 各自定义与典型值);
尺度效应 (混合像元); 主流卫星平台参数表 (高度/重访/幅宽)。

**D2 physics — 提取**: 电磁波谱与遥感波段划分 (可见光/近红外/短波红外/
热红外/微波, 波长范围); 大气窗口 (各窗口波长与用途); 辐射传输路径
(太阳-大气-地表-传感器的四段能量关系); 反射率三种形态 (表观/地表/真实)
与换算; 地物波谱曲线 (植被/水体/土壤/冰雪/城市的特征形状与成因);
散射类型 (瑞利/米氏/无选择性) 与影响。

**D3 image-processing — 提取**: 辐射定标链 (DN→辐亮度→表观反射率→地表
反射率的每步公式与系数来源); 几何校正与正射化; 大气校正方法对比
(DOS/6S/FLAASH/Sen2Cor); 云掩膜方法 (QA波段/Fmask/cloudScore);
图像增强 (直方图拉伸/滤波); 裁剪镶嵌重投影与重采样方式选择;
合成方法 (中值/最绿像元/质量Mosaic)。

**D4 analysis — 提取**: 指数族全集 (NDVI/MNDWI/NDBI/NDSI/NDWI/NBR/AWEI/EVI...
每个含公式/原始出处/适用边界); 阈值判读基准 (三元组形态); 分类方法
(非监督/监督/面向对象/深度学习, 各自流程与适用数据量); 变化检测方法论
(代数法/后分类对比/时序分析/CVA); 精度验证 (混淆矩阵/Kappa/分层抽样)。

**D5 applications — 提取**: 行业应用的领域知识包, 每包含——常用数据组合 /
指标集 / 典型阈值 / 成果形态 / 行业规范引用。首期五包: 土地覆盖与利用、
植被与农业 (估产/长势)、水体与湿地 (提取/洪水)、城市 (热岛/不透水面/
扩张)、灾害 (火灾NBR/滑坡/淹没)。每包须联动 O1 模板 (recommended_template)。

### 2.3 词条目录映射 (type × 域)

六类词条 type 是词条的**体裁**, 五大域是知识的**分册**; 一个域下会有多种
体裁。文件物理位置按域存放:

```
src/knowledge/wiki/
├── SPEC.md                  ← 本文件
├── _index.json              ← 主索引 (工具生成, 禁止手改)
├── fundamentals/            ← D1 (concept/satellites/sensors 词条为主)
├── physics/                 ← D2 (physics 词条)
├── image-processing/        ← D3 (methods-preprocessing 词条)
├── analysis/                ← D4 (indices/methods-analysis 词条)
│   ├── indices/
│   └── methods/
├── applications/            ← D5 (application-pack 词条)
└── pitfalls/                ← 横切反模式库 (实测翻车模式, 与 failure_store 呼应)
└── sources → 见 §3 (位于 knowledge/sources/, 词条的唯一合法上游)
```

```
src/knowledge/sources/             ← 原始知识源 (词条的唯一合法上游)
    ├── _sources.json              来源注册表 (source_id → 书目元数据)
    ├── book-meianxin-daolun.md    每本书/每篇论文一个摘录文件
    ├── paper-xu2006-mndwi.md
    └── gee-cloud-book.md
```

数据流: **sources/ (原始摘录) → 提取 checklist → wiki 词条 → build 生成 _index.json**
逆向禁止: 不允许先写词条再补出处。

## 3. 原始知识源规范 (sources/)

每个来源一个 md 文件, 头部 YAML 注册元数据:

```yaml
---
source_id: paper-xu2006-mndwi     # 全局唯一, 小写连字符
type: paper                        # book | paper | doc(官方文档) | standard
title: "Modification of the Normalized Difference Water Index"
authors: ["Xu Hanqiu"]
year: 2006
venue: "International Journal of Remote Sensing, 27(14)"
pages_or_doi: "pp.3025-3033 / 10.1080/01431160500354953"
reliability: high                  # high=同行评审/官方 | medium=教材 | low=博客(禁用为唯一出处)
---
```

正文按**知识点片段**记录原文摘录, 每片带定位锚点:

```markdown
## [P3027] MNDWI 定义与波段选择
原文要点: 用 Green 与 MIR(中红外) 构建归一化差异指数, 相比 NDWI 能
显著抑制建筑区的提取噪音。
> 关键句: "the MIR band is superior to the NIR band for water body
> extraction in built-up areas"

## [P3028] 阈值建议
原文要点: 实验中 MNDWI 阈值 0.0-0.3 区间均可分离水体, 城区推荐 0.2 起。
```

锚点格式 `[P页码]` 或 `[§章节]` 或 `[DOI]`; 词条引用时必须带上这些锚点。

## 4. 词条规范 (wiki/<topic>/*.md)

### 4.1 Frontmatter (机器校验段, 缺一不可)

```yaml
---
id: mndwi                          # 小写连字符, = 文件名
domain: analysis                   # 所属主题域: fundamentals|physics|image-processing|analysis|applications
type: indices                      # 词条体裁: indices|satellites|sensors|methods|concept|application-pack|pitfalls
title: MNDWI                       # 展示名
aliases:                           # 至少含 中文名 + 英文全称 + 缩写 各一条
  - 改进归一化差异水体指数
  - Modified Normalized Difference Water Index
themes: [indices, water]           # 主题标签 (用于 L2 主题路由, 见 §6 词表)
sources:                           # ≥1 条, 每条 = source_id + 锚点
  - {source_id: paper-xu2006-mndwi, anchor: "[P3027]"}
verified: true                     # 双检通过标记 (见 §7)
related: [ndwi, water-extraction, sentinel2]
template_hint: water-extraction    # 可选: 推荐模板 ID (O1 模板管线联动)
---
```

### 4.2 正文章节 (遥感专业六段式, 顺序固定)

```markdown
## 定义
MNDWI = (Green − SWIR) / (Green + SWIR)
<!-- 公式必须显式写出波段对; 指数类词条此段必填 -->

## 适用条件
- 传感器: 光学多光谱 (需 Green + SWIR 波段); 不适用于 SAR
- 场景: 城市/建成区水体提取优于 NDWI
- 尺度: 10-30m 分辨率下验证有效

## 判读基准
- MNDWI > 0.2 判水体 (城区, 夏季, Sentinel-2)   ← 阈值三元组: 值+场景+条件
- 大面积陆地区域均值常为负 (-0.1 ~ -0.3) —— 属正常形态, 非异常

## 常见错误
- 与 NDWI 混用波段 (NDWI 用 NIR, MNDWI 用 SWIR)
- 对雷达影像套用光学指数

## 边界与局限
- 云/冰雪会干扰 SWIR; 高浑浊水体阈值需下调

## 关联词条
[[ndwi]], [[water-extraction]], [[sentinel2]]
```

各 type 的强制章节差异:
- **indices**: 定义(含公式) / 适用条件 / 判读基准 / 常见错误 必填
- **satellites/sensors**: 波段表(编号+波长+分辨率) / 重访周期 / 数据集ID 必填
- **methods**: 流程步骤 / 输入输出 / 精度参考 / 常见失败模式 必填
- **physics**: 公式推导 / 遥感量纲 / 常见误解 必填
- **application-pack** (D5 应用包): 数据组合 / 指标集与阈值 / 成果形态 /
  行业规范引用 / recommended_template 必填
- **pitfalls**: 现象 / 根因 / 规避方法 必填

### 4.3 数值与术语纪律

- 所有物理量带单位 (`0.0001`, `%`, `μm`); 无单位数值必须注明含义
- 阈值用**三元组**表达: `值 + 场景 + 条件` (见 4.2 示例)
- 中文术语首次出现括注英文; 缩写首次出现给全称
- 禁止口语化断言 ("一般来说效果不错") —— 写成可判定的条件句

## 5. 主索引 _index.json (工具生成)

由 `python -m src.knowledge.wiki_build` 扫描全部词条生成, **禁止手改**:

```json
{
  "spec_version": "1.0",
  "entries": {
    "mndwi": {
      "file": "indices/mndwi.md",
      "title": "MNDWI",
      "aliases": ["改进归一化差异水体指数", "..."],
      "themes": ["indices", "water"],
      "verified": true,
      "template_hint": "water-extraction"
    }
  },
  "theme_words": {
    "水体": ["water-extraction", "mndwi", "ndwi"],
    "植被": ["ndvi", "land-cover-classification"],
    "变化检测": ["change-detection"],
    "云": ["cloud-masking", "sentinel2"]
  }
}
```

- `entries`: id/title/aliases/themes → L1 精确命中与 L3 包含匹配的数据源
- `theme_words`: 中文任务词表 → L2 主题路由 (人工维护, 变更须过评审)
  规范载体: `wiki/_theme_words.json` (人工维护的唯一手改文件),
  build 时校验其引用的词条 id 并合并写入 _index.json
- 生成器同时执行 §7 校验, 任一词条不合格即构建失败 (响亮失败原则)

## 6. 检索路由 (消费方契约, wiki_kb.py 实现)

```
L1 精确: 任务文本分词命中 id/title/alias (大小写/全半角归一)
L2 主题: theme_words 中文词 → 词条集合
L3 包含: alias 是任务文本子串
排序: L1 > L2 > L3; 同级按 related 度; 至多取 3 条 × 800 字注入 prompt
prompt 注入时附注: "以下为已核实知识库条目, 引用库外知识需声明不确定"
```

## 7. 质量门槛与双检流程

新词条入库 checklist (PR 描述里逐项勾选):

- [ ] frontmatter 通过 schema 校验 (type/themes/aliases/sources 齐备)
- [ ] 每条事实能在 sources/ 中找到对应锚点原文
- [ ] 阈值均为三元组形态
- [ ] aliases 含中文+英文+缩写
- [ ] 相关词条双向 linked ([[x]] 与对方 related 互指)
- [ ] 由第二人 (或另一次独立会话) 对照原文复核后置 verified: true

`verified: false` 的词条可以存在但**不会被检索召回** (防未核实内容泄入 prompt)。

## 8. 工具链约定

| 工具 | 职责 |
|---|---|
| `python -m src.knowledge.wiki_build` | 校验全部词条 + 重建 _index.json |
| `src.knowledge.wiki_kb.search_wiki(text)` | 三层路由检索 (消费方唯一入口) |
| `RS-agent/scripts/wiki_add_source.py` | 新来源注册脚手架 (生成 sources 元数据骨架) |

## 9. 版本

WIKI_SPEC_VERSION = 1.1 (2026-08-26)
变更记录:
- 1.0 (2026-08-26): 初版。
- 1.1 (2026-08-26): 五大主题域 D1-D5 提取清单与词条目录映射 (§2.1-2.3);
  theme_words 规范载体定为 wiki/_theme_words.json (§5)。
不兼容变更须递增主版本并在 24 号/26 号存档登记迁移方案。
