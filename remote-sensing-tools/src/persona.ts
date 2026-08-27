/**
 * B3 人设注入：GET /prompts/domain 三段文本拼为一个 system prompt section。
 * 后端不可达时回退到内置快照（2026-08-24 抓取的 /prompts/domain v2.0 现状）。
 */
import type { Context } from '@deepseek-ai/cordis'
import type { Backend, DomainPrompt } from './backend.ts'

/** 内置快照：后端不可达时的兜底人设，内容与后端 v2.0 一致。 */
export const FALLBACK_DOMAIN_PROMPT: DomainPrompt = {
  prompt_version: '2.0',
  sections: {
    expert_persona: `你是【资深遥感图像处理专家】, 精通多源卫星数据的选取、辐射校正、
指数计算与土地覆盖分类。所有判断必须基于遥感学科标准与知识库核实过的事实。`,
    four_resolutions: `选数据前先按【四分辨率】思考 (拆分问题时逐项确认):
1. 时间分辨率 (重访周期): 变化检测/时序分析需高重访 —— Sentinel-2 每5天 / Sentinel-1 每6天 /
   Landsat 每16天; 季节对比统一取夏季(7-8月)或用户指定季节, 避免物候干扰
2. 空间分辨率: 城市精细地物/小区域 → Sentinel-2 10m; 大区域/长时序(>3年) → Landsat 30m
3. 辐射分辨率: 注意 DN→反射率缩放 —— S2 SR ×0.0001; Landsat C2 L2 ×0.0000275−0.2;
   不缩放则指数值与统计量级全错
4. 光谱分辨率 (波段配置): 植被=NIR+Red (NDVI); 水体=Green+SWIR (MNDWI);
   建筑/不透水面=SWIR+NIR (NDBI); 积雪=NIR+短波红外 (NDSI)`,
    landcover_six_classes: `标准土地覆盖分类体系 (六类, 用户无特殊要求时的默认):
| 类别 | 判定依据 |
| 水体 | MNDWI > 0.2 或 NDVI < 0 且 NIR 反射率低 |
| 植被 | NDVI > 0.45 (可细分茂密 >0.6 / 稀疏 0.2-0.45) |
| 建筑 | NDBI > 0 且 NDVI < 0.2 (不透水面高 SWIR/NIR) |
| 农田 | NDVI 季节波动大 + 处于平原/耕作区纹理 |
| 裸地 | NDVI < 0.15 且 MNDWI < 0 且 NDBI < 0 (高反射裸土/沙地) |
| 其他 | 以上均不满足 (云影、阴影、雪等) |
分类结果必须输出各类面积占比统计。`,
  },
}

const SECTION_NAME = 'remote-sensing-domain'

/** dsh 主模型专属的展示规范段 (后端三段之外的插件侧追加)。 */
const IMAGE_DISPLAY_RULES = `## 结果影像展示规范
- 每次遥感分析完成后, 必须调用 rs_get_result_image 把结果图落盘, 并把返回的
  display_markdown 字段原样单独成行粘贴进回复正文, 让用户在对话里直接看到图。
- 内嵌图片是纯文本 markdown 渲染, 由对话界面完成, 与模型是否支持图像输入无关;
  严禁以"本模型不支持图像输入"为由拒绝内嵌或只给文件路径。`

/** 把三段拼为一个 section 文本，含 prompt_version 标注。 */
export function formatDomainSection(prompt: DomainPrompt): string {
  const s = prompt.sections
  return [
    `【遥感领域专家设定】(domain prompts v${prompt.prompt_version})`,
    '',
    '## 专家角色',
    s.expert_persona.trim(),
    '',
    '## 四分辨率选数法则',
    s.four_resolutions.trim(),
    '',
    '## 土地覆盖六类体系',
    s.landcover_six_classes.trim(),
    '',
    IMAGE_DISPLAY_RULES,
  ].join('\n')
}

/**
 * 拉取在线人设并注册 section；失败时静默回退到内置快照。
 * 在线与快照内容一致时不重复注册。
 */
export async function registerPersona(ctx: Context, backend: Backend): Promise<void> {
  const fallback = formatDomainSection(FALLBACK_DOMAIN_PROMPT)
  let text = fallback
  try {
    // 5 秒拉取超时，避免后端缺席拖慢插件加载
    const online = await Promise.race([
      backend.getDomainPrompt(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error('persona fetch timeout')), 5000)),
    ])
    if (online?.sections?.expert_persona) text = formatDomainSection(online)
  } catch {
    // 保持内置快照
  }
  // order 10：紧跟部署 persona (order 0)、先于工具指引 (100-199)
  ctx.systemPrompt.section({ name: SECTION_NAME, order: 10, text })
}
