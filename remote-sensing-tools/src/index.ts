/**
 * remote-sensing-tools —— 遥感 Agent × dsh 原生插件 (B2-full + B3 + B4)。
 * 五个经 FastAPI 后端的工具 + 领域人设注入 + 导出审批/落盘围栏。
 */
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { Backend } from './backend.ts'
import { registerPersona } from './persona.ts'
import { registerTools } from './tools.ts'
import { setupUiPolish } from './ui-polish.ts'

export const name = 'remote-sensing-tools'

/** systemPrompt 服务按需注入（personaEnabled=false 时不强依赖） */
export const inject = ['tools', 'systemPrompt']

export interface Config {
  /** 后端 FastAPI 地址 */
  apiBase: string
  /** Bearer token；留空时回退读环境变量 REMOTE_SENSING_TOKEN */
  token: string
  /** 固定用户标识 */
  userId: string
  /** 结果影像允许落盘的根目录（B4 围栏） */
  workspaceDir: string
  /** rs_execute_analysis 阻塞等待上限（硬顶 900000ms） */
  waitTimeoutMs: number
  /** 任务状态轮询间隔 */
  pollIntervalMs: number
  /** 是否注入遥感领域人设 section */
  personaEnabled: boolean
  /** UI 润色: 隐藏会话流中的「上下文注入」行 (仅 web 组合生效) */
  hideContextInjection: boolean
}

export const Config: z<Config> = z.object({
  apiBase: z.string().default('http://127.0.0.1:8000'),
  token: z.string().default(''),
  userId: z.string().default('rs-a-user'),
  workspaceDir: z.string().default('D:/rs-agent-workspace'),
  waitTimeoutMs: z.number().max(900_000).default(600_000),
  pollIntervalMs: z.number().min(1_000).default(3_000),
  personaEnabled: z.boolean().default(true),
  hideContextInjection: z.boolean().default(true),
})

export async function apply(ctx: Context, config: Config): Promise<void> {
  const backend = new Backend({
    apiBase: config.apiBase.replace(/\/+$/, ''),
    // 凭证零外泄：token 只在本进程内使用，优先配置、回退环境变量
    token: config.token || process.env.REMOTE_SENSING_TOKEN || '',
    userId: config.userId,
  })

  if (config.personaEnabled) {
    await registerPersona(ctx, backend)
  }

  setupUiPolish(ctx, config.hideContextInjection)
  setupCredentialRoute(ctx, config)
  setupImageRoute(ctx, backend)

  registerTools(ctx, config, backend, imageBaseUrl(ctx))
}

/** /rs-image/<task_id> 图片代理基址 (绝对 http URL); 无 webServer 的组合返回 null。 */
function imageBaseUrl(ctx: Context): string | null {
  const webServer = ctx.get('webServer')
  if (!webServer) return null
  return `http://${webServer.host}:${webServer.port}`
}

/**
 * /rs-image/<task_id> 本地同源路由 (web 组合专属): 服务端持 Bearer 拉后端
 * 结果 JPEG 回给 <img>。对话内嵌展示的地基 —— dsh 前端 markdown 图片只放行
 * 绝对 http(s) URL 且 <img> 带不了 Authorization 头, 同源代理一次解决两难。
 * 密级同 /rs-auth-token: token 只在 dsh 进程内使用, 不新增外泄面。
 */
function setupImageRoute(ctx: Context, backend: Backend): void {
  const webServer = ctx.get('webServer')
  if (!webServer) return
  webServer.register({
    kind: 'prefix',
    path: '/rs-image',
    handler: async (req, res) => {
      // task_id 白名单: 后端 id 为 12 位 hex; 拒绝任意路径/查询注入
      const m = /^\/rs-image\/([A-Za-z0-9_-]{1,64})$/.exec(req.url ?? '')
      if (!m) {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
        res.end('not found')
        return
      }
      try {
        const buf = await backend.getResultImage(m[1])
        res.writeHead(200, {
          'Content-Type': 'image/jpeg',
          'Content-Length': buf.byteLength,
          // 任务结果是 immutable 产物: 会话重渲染/翻历史不重复拉
          'Cache-Control': 'private, max-age=86400',
        })
        res.end(Buffer.from(buf))
      } catch (e) {
        res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
        res.end(`结果图获取失败: ${e instanceof Error ? e.message : String(e)}`)
      }
    },
  })
}

/**
 * /rs-auth-token 本地路由 (web 组合专属): 浏览器凭证面板经同源此路由取
 * Bearer/user_id, 再直连后端做凭证状态查询与换绑。
 * 密级说明: 返回的 token 与本插件 env/config 持有的同一 Bearer —— 只从
 * 本机 dsh web 暴露, 不新增任何外泄面; headless 等无 webServer 组合静默跳过。
 */
function setupCredentialRoute(ctx: Context, config: Config): void {
  const webServer = ctx.get('webServer')
  if (!webServer) return
  const token = config.token || process.env.REMOTE_SENSING_TOKEN || ''
  webServer.register({
    kind: 'exact',
    path: '/rs-auth-token',
    handler: (_req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ token, userId: config.userId }))
    },
  })
}
