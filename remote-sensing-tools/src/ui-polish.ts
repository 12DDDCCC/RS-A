/**
 * UI 润色 (UI批次): web 会话界面按需精简。
 *
 * 1. 隐藏消息流中的「上下文注入」行 (system prompt / skill-catalog 卡)。
 *    dsh 的 ContextInjectionRow 默认折叠但仍占一行; 对话密度优先时整行隐藏。
 *
 * 注入走 webserver 的结构化 index-inject 表 (kind:'style'), 只在 web 组合下
 * 生效 —— headless 等无 webServer 服务的 profile 静默跳过, 不进 inject 硬依赖。
 *
 * ⚠ 类名绑定构建哈希 (dsh 0.1.1-rc.2 的 pC0e7a CSS module, 实测全页恰好
 *   只命中注入行、零误伤); dsh 升级后若失效, 复核此处选择器。
 */
import type { Context } from '@deepseek-ai/cordis'
import type { IndexInjection } from '@deepseek-ai/dsh-host-webserver'
import '@deepseek-ai/dsh-host-webserver' // side-effect: Events 接口声明合并 (webserver/index-inject)

const HIDE_INJECTION_ROWS_CSS = `
/* RS-Agent UI 润色: 隐藏“上下文注入”行 (类名绑定 dsh 0.1.1-rc.2 构建哈希) */
[class*="pC0e7a_root"] { display: none !important; }
`

export function setupUiPolish(ctx: Context, hideContextInjection: boolean): void {
  if (!hideContextInjection) return
  const webServer = ctx.get('webServer')
  if (!webServer) return
  ctx.on('webserver/index-inject', (table: IndexInjection[]) => {
    table.push({ kind: 'style', text: HIDE_INJECTION_ROWS_CSS })
  })
}
