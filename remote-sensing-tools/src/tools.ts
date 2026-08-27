/**
 * 五个模型可见工具。全部经 fetch 打 FastAPI 后端，后端为唯一事实源。
 * B4 安全铁律在 rs_get_result_image 的 save_path 围栏与 rs_execute_analysis 的审批钩子里实现。
 */
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve, sep } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import type { ApprovalService } from '@deepseek-ai/dsh-user-approval'
import { Backend, sleep, type TaskSnapshot } from './backend.ts'

// ToolRunContext 未从 @deepseek-ai/dsh-tools 根导出，这里按结构最小化声明
interface ExecLike {
  readonly agent?: Parameters<ApprovalService['request']>[0]['agent']
  readonly signal: AbortSignal
}

/** 工具层需要的插件配置切片。 */
export interface ToolConfig {
  workspaceDir: string
  waitTimeoutMs: number
  pollIntervalMs: number
}

/** 阻塞式等待上限硬顶 15 分钟，Config 值再大也不越过。 */
const MAX_WAIT_MS = 900_000

/**
 * B4 落盘围栏：save_path 强制解析后必须位于 workspaceDir 之内，越界一律拒绝。
 * Windows 下按大小写不敏感比较。
 */
export function resolveInsideWorkspace(workspaceDir: string, savePath: string): string {
  const norm = (p: string) => (process.platform === 'win32' ? p.toLowerCase() : p)
  const root = resolve(workspaceDir)
  const target = resolve(savePath)
  if (norm(target) !== norm(root) && !norm(target).startsWith(norm(root) + sep)) {
    throw new Error(`save_path 越界被拒绝: "${savePath}" 解析后不在工作区 "${root}" 之内`)
  }
  return target
}

/**
 * B4 审批钩子：经 ctx.approval 请求人工批准。
 * 服务缺席 / 无关联 Agent / 非 allowed-once 结果一律拒绝执行并说明原因（fail closed）。
 */
async function requireExportApproval(ctx: Context, exec: ExecLike, detail: string): Promise<void> {
  // 与官方 tools 包相同的 opportunistic 取法：部署未组合 ApprovalService 时为 undefined
  const approval = ctx.get('approval') as ApprovalService | undefined
  if (!approval) throw new Error('审批服务不可用（当前部署未组合 approval provider），已拒绝执行导出分析；可去掉 export 参数重试')
  if (!exec.agent) throw new Error('本次调用没有关联的 Agent 会话，无法发起人工审批，已拒绝执行导出分析')
  try {
    const outcome = await approval.request({
      agent: exec.agent,
      toolName: 'rs_execute_analysis',
      reason: `导出分析需人工批准: ${detail}`,
      signal: exec.signal,
    })
    if (outcome !== 'allowed-once') {
      throw new Error(`人工审批未通过 (${outcome})，已拒绝执行导出分析`)
    }
  } catch (e) {
    if (e instanceof Error && /审批/.test(e.message)) throw e
    throw new Error(`审批请求失败，已按拒绝处理: ${e instanceof Error ? e.message : String(e)}`)
  }
}

const SNAPSHOT_PROPERTIES = {
  status: { type: 'string' as const, description: 'queued/running/need_clarify/done/failed' },
  phase_text: { type: 'string' as const, description: '当前处理阶段说明' },
  question: { type: 'string' as const, description: 'need_clarify 时的反问问题' },
  caption: { type: 'string' as const, description: 'done 时的分析结论文本' },
  error: {
    type: 'object' as const,
    additionalProperties: false,
    description: 'failed 时的结构化错误',
    properties: {
      code: { type: 'string' as const },
      message: { type: 'string' as const },
      suggestion: { type: 'string' as const },
    },
  },
}

/** 把任务快照压成无 undefined 键的对象，满足输出契约校验。 */
function compactSnapshot(snap: TaskSnapshot): Record<string, unknown> {
  const out: Record<string, unknown> = { status: snap.status }
  if (snap.phase_text != null) out.phase_text = snap.phase_text
  if (snap.question != null) out.question = snap.question
  if (snap.caption != null) out.caption = snap.caption
  if (snap.error?.code || snap.error?.message || snap.error?.suggestion) out.error = {
    ...(snap.error.code != null ? { code: snap.error.code } : {}),
    ...(snap.error.message != null ? { message: snap.error.message } : {}),
    ...(snap.error.suggestion != null ? { suggestion: snap.error.suggestion } : {}),
  }
  return out
}

/** 快照对象转模型可读文本。 */
export function snapshotToText(s: Record<string, unknown>): string {
  const lines: string[] = [`状态: ${s.status ?? '未知'}`]
  if (typeof s.phase_text === 'string') lines.push(`阶段: ${s.phase_text}`)
  if (typeof s.question === 'string') lines.push(`反问: ${s.question}`)
  if (typeof s.caption === 'string') lines.push(`结论: ${s.caption}`)
  const err = s.error as { code?: string; message?: string; suggestion?: string } | undefined
  if (err) lines.push(`错误: [${err.code ?? '?'}] ${err.message ?? ''}${err.suggestion ? ` 建议: ${err.suggestion}` : ''}`)
  if (typeof s.task_id === 'string' && !lines.some((l) => l.includes(s.task_id as string))) lines.unshift(`task_id: ${s.task_id}`)
  return lines.join('\n')
}

const textRender = (_args: unknown, value: string): ContentBlock[] => [{ type: 'text', text: value }]

/** 注册全部五个工具。imageBase: /rs-image 代理的绝对基址 (无 webServer 时 null, 退化纯文本)。 */
export function registerTools(
  ctx: Context, cfg: ToolConfig, backend: Backend, imageBase: string | null,
): void {
  // 1. 数据集目录 —— 供主模型按四分辨率选数据
  ctx.tools.register(defineTool({
    name: 'rs_get_dataset_catalog',
    description: '获取可用卫星数据集目录（key、GEE 集合 ID、时间/空间分辨率、波段）。选取数据源前必须先调用本工具。',
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: textRender,
    },
    timeoutMs: 30_000,
    async execute() {
      const catalog = await backend.getCatalog()
      const ds = catalog.datasets ?? []
      if (ds.length === 0) return '数据集目录为空'
      const lines = ds.map((d) =>
        `- ${d.key} | ${d.full_name} | GEE: ${d.gee_collection_id}`
        + `${d.resolution_m != null ? ` | 空间分辨率: ${d.resolution_m}m` : ''}`
        + `${d.temporal_coverage ? ` | 时间覆盖: ${d.temporal_coverage}` : ''}`
        + ` | 波段: ${Object.keys(d.bands ?? {}).join(', ')}`)
      return `共 ${ds.length} 个数据集:\n${lines.join('\n')}`
    },
  }))

  // 2. 领域代码自查 —— 提交分析前验证领域代码合法性
  ctx.tools.register(defineTool({
    name: 'rs_verify_domain_code',
    description: '校验一段领域分析代码是否合规。提交 rs_execute_analysis 前建议先自查；返回 PASS 或逐条问题列表。',
    parameters: {
      code: { type: 'string', required: true, description: '待校验的领域分析代码全文' },
    },
    output: {
      schema: { type: 'string' },
      render: textRender,
    },
    timeoutMs: 120_000,
    async execute(args) {
      const r = await backend.verifyDomainCode(args.code)
      if (r.passed) return 'PASS: 领域代码校验通过'
      return `未通过，共 ${r.issues.length} 个问题:\n${(r.issues ?? []).map((i) => `- ${i}`).join('\n')}`
    },
  }))

  // 3. 阻塞式分析执行（含澄清续跑 + 导出审批钩子）
  ctx.tools.register(defineTool({
    name: 'rs_execute_analysis',
    description: '提交遥感分析任务并阻塞等待结果。首轮传 user_input(+place)；收到反问后把原 task_id 连同 clarifications 一起传入以续跑。返回 need_clarify(反问)/done(结论+task_id)/failed(人话错误)。export=true 表示需要导出成果，会先请求人工批准。',
    parameters: {
      user_input: { type: 'string', description: '完整的分析需求描述（含区域、时间范围、指标）' },
      place: { type: 'string', description: '地点名，可选：优先给用户原话中的最小行政单元（区/县名如"江宁区"），不要归并到上级市——后端按区县级默认精度解析' },
      task_id: { type: 'string', description: '澄清续跑时传入原任务 id' },
      clarifications: { type: 'string', description: '对反问问题的回答，仅与 task_id 同时使用' },
      export: { type: 'boolean', description: 'true 时先经人工审批再执行。注意: max 挡高清 JPEG 无需 export(分块直出), 设了会触发审批等待并可能耗尽工具超时——仅用户明确要 GeoTIFF/无压缩导出时才设 true' },
      quality: { type: 'string', description: '出图挡位: standard(默认,<1MB) | high(1-10MB,43m/px) | max(高清大图,分块取数,JPEG 可超 30MB)。用户要高清/大图时选 max; 选 max 时不要设置 export 参数' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          status: { type: 'string', required: true, description: 'need_clarify/done/failed/running_timeout' },
          task_id: { type: 'string', description: '任务 id，续跑与取图都要用它' },
          question: { type: 'string', description: 'need_clarify 时向用户提出的反问' },
          caption: { type: 'string', description: 'done 时的分析结论文本' },
          message: { type: 'string', description: '给模型的下一步提示或错误说明' },
        },
      },
      render: (_args, value) => [{ type: 'text', text: snapshotToText(value as Record<string, unknown>) }],
    },
    // 略大于阻塞上限的合作式超时预算
    timeoutMs: Math.min(cfg.waitTimeoutMs, MAX_WAIT_MS) + 60_000,
    async execute(args, exec) {
      if (args.export === true) {
        await requireExportApproval(ctx, exec, (args.user_input ?? '').slice(0, 120))
      }

      let taskId = args.task_id
      if (taskId && typeof args.clarifications === 'string') {
        await backend.answer(taskId, args.clarifications)
      } else if (!taskId) {
        if (!args.user_input) {
          return { status: 'failed', task_id: '', message: '缺少必填参数 user_input；澄清续跑请同时传 task_id 与 clarifications' }
        }
        const effQuality = typeof args.quality === 'string' && args.quality
    ? args.quality
    : (args.export === true ? 'max' : undefined)  // 导出默认最高清晰度
const created = await backend.analyze(args.user_input, typeof args.place === 'string' ? args.place : undefined, effQuality)
        taskId = created.task_id
      }

      // 每 3 秒轮询直到终态或超时；瞬时网络抖动容忍连续 5 次
      const deadline = Date.now() + Math.min(cfg.waitTimeoutMs, MAX_WAIT_MS)
      let consecutiveErrors = 0
      for (;;) {
        if (exec.signal.aborted) throw new Error('调用已被取消')
        let snap: TaskSnapshot
        try {
          snap = await backend.getTask(taskId)
          consecutiveErrors = 0
        } catch (e) {
          if (++consecutiveErrors >= 5) {
            return { status: 'failed', task_id: taskId, message: `轮询任务状态连续失败: ${e instanceof Error ? e.message : String(e)}` }
          }
          await sleep(cfg.pollIntervalMs, exec.signal)
          continue
        }
        if (snap.status === 'need_clarify') {
          return { status: 'need_clarify', task_id: taskId, question: snap.question ?? '(后端未给出问题文本)', message: '请把反问转述给用户；拿到回答后带 task_id+clarifications 再次调用本工具续跑' }
        }
        if (snap.status === 'done') {
          return { status: 'done', task_id: taskId, caption: snap.caption ?? '', message: '结果影像稍后可用 rs_get_result_image(task_id, save_path) 落盘并在对话中展示给用户' }
        }
        if (snap.status === 'failed') {
          const err = snap.error ?? {}
          return { status: 'failed', task_id: taskId, message: `分析失败 [${err.code ?? 'UNKNOWN'}] ${err.message ?? '未知错误'}${err.suggestion ? `。建议: ${err.suggestion}` : ''}` }
        }
        if (Date.now() >= deadline) {
          return { status: 'running_timeout', task_id: taskId, message: `等待超过 ${Math.round(Math.min(cfg.waitTimeoutMs, MAX_WAIT_MS) / 1000)} 秒仍未完成；可稍后用 rs_get_task_status(task_id) 查询进度` }
        }
        await sleep(cfg.pollIntervalMs, exec.signal)
      }
    },
  }))

  // 4. 任务状态快照
  ctx.tools.register(defineTool({
    name: 'rs_get_task_status',
    description: '查询指定遥感分析任务的即时状态快照（阶段、反问、结论或错误）。适用于 rs_execute_analysis 超时后的补查。',
    parameters: {
      task_id: { type: 'string', required: true, description: '任务 id' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: SNAPSHOT_PROPERTIES,
      },
      render: (_args, value) => [{ type: 'text', text: snapshotToText(value as Record<string, unknown>) }],
    },
    timeoutMs: 30_000,
    async execute(args) {
      return compactSnapshot(await backend.getTask(args.task_id))
    },
  }))

  // 5. 结果影像落盘（B4 围栏）
  ctx.tools.register(defineTool({
    name: 'rs_get_result_image',
    description: `下载指定任务的 JPEG 结果影像并保存到本地，并在对话中直接展示该图。save_path 必须位于工作区目录之内，越界一律拒绝。`,
    parameters: {
      task_id: { type: 'string', required: true, description: '已完成 (done) 的任务 id' },
      save_path: { type: 'string', required: true, description: `保存路径，必须在 ${cfg.workspaceDir} 之内` },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          path: { type: 'string', required: true, description: '落盘绝对路径' },
          bytes: { type: 'integer', required: true, description: '文件字节数' },
        },
      },
      // render 输出即模型可见的工具结果: 除状态行外, 附上可照抄的内嵌
      // markdown —— dsh 前端 markdown 图片仅放行绝对 http(s) URL, 经
      // /rs-image 同源代理 (服务端持 Bearer) 取流; 无 webServer 时退化纯文本
      render: (args, value) => {
        const lines = [`结果影像已保存 (${value.bytes} 字节)`, `路径: ${value.path}`]
        if (imageBase && typeof args.task_id === 'string') {
          lines.push('必须在最终回复正文中原样单独成行粘贴下面这行, 把结果图内嵌展示给用户 (由界面渲染, 与模型图像输入能力无关):')
          lines.push(`![遥感分析结果](${imageBase}/rs-image/${args.task_id})`)
        }
        return [{ type: 'text', text: lines.join('\n') }]
      },
    },
    timeoutMs: 120_000,
    async execute(args) {
      const target = resolveInsideWorkspace(cfg.workspaceDir, args.save_path)
      const buf = await backend.getResultImage(args.task_id)
      await mkdir(dirname(target), { recursive: true })
      await writeFile(target, Buffer.from(buf))
      return { path: target, bytes: buf.byteLength }
    },
  }))
}
