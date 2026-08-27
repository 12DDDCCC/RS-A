/**
 * FastAPI 后端 (:8000) HTTP 客户端。后端是唯一事实源：本文件只按契约发请求，
 * 不在插件侧复刻任何业务逻辑。
 */

/** GET /tasks/{id} 返回的任务状态快照（契约见后端 OpenAPI）。 */
export interface TaskSnapshot {
  status: 'queued' | 'running' | 'need_clarify' | 'done' | 'failed'
  phase_text?: string
  question?: string
  caption?: string
  error?: { code?: string; message?: string; suggestion?: string }
}

/** GET /prompts/domain 返回的领域人设三段文本。 */
export interface DomainPrompt {
  prompt_version: string
  sections: {
    expert_persona: string
    four_resolutions: string
    landcover_six_classes: string
  }
}

export interface CatalogDataset {
  key: string
  full_name: string
  gee_collection_id: string
  bands: Record<string, unknown>
  temporal_coverage?: string
  resolution_m?: number
}

export interface Catalog {
  prompt_version?: string
  datasets: CatalogDataset[]
}

/** 非 2xx 响应的统一错误；message 面向模型可读。 */
export class BackendError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'BackendError'
    this.status = status
  }
}

export interface BackendOptions {
  apiBase: string
  token: string
  userId: string
}

export class Backend {
  private readonly opts: BackendOptions
  constructor(opts: BackendOptions) {
    this.opts = opts
  }

  private async request(path: string, init?: RequestInit): Promise<Response> {
    const res = await fetch(`${this.opts.apiBase}${path}`, init)
    if (!res.ok) {
      let detail = ''
      try { detail = (await res.text()).slice(0, 300) } catch {}
      throw new BackendError(res.status, `后端 ${init?.method ?? 'GET'} ${path} 失败 (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
    }
    return res
  }

  private json(path: string, method = 'GET', body?: unknown, auth = true): Promise<unknown> {
    return this.request(path, {
      method,
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        // 无鉴权端点 (/domain/verify、/prompts/domain、/knowledge/catalog) auth=false
        ...(auth && this.opts.token ? { Authorization: `Bearer ${this.opts.token}` } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }).then((r) => r.json())
  }

  /** POST /analyze → 202 {task_id, status:"queued", session_id} */
  async analyze(userInput: string, place?: string,
                quality?: string): Promise<{ task_id: string }> {
    const data = await this.json('/analyze', 'POST', {
      user_input: userInput,
      user_id: this.opts.userId,
      ...(place ? { place } : {}),
      ...(quality ? { quality } : {}),
    }) as { task_id?: string }
    if (!data?.task_id) throw new Error('后端 /analyze 未返回 task_id')
    return { task_id: data.task_id }
  }

  /** POST /tasks/{id}/answer → 澄清续跑 */
  answer(taskId: string, answerText: string): Promise<unknown> {
    return this.json(`/tasks/${encodeURIComponent(taskId)}/answer`, 'POST', { answer: answerText })
  }

  /** GET /tasks/{id} → 状态快照 */
  getTask(taskId: string): Promise<TaskSnapshot> {
    return this.json(`/tasks/${encodeURIComponent(taskId)}`) as Promise<TaskSnapshot>
  }

  /** GET /tasks/{id}/result → JPEG 二进制（仅 done；需 Bearer —— 归属校验） */
  async getResultImage(taskId: string): Promise<ArrayBuffer> {
    const res = await this.request(`/tasks/${encodeURIComponent(taskId)}/result`, {
      // 二进制响应不走 json() 封装, 鉴权头在此显式携带 (漏带即 401, 实测案例)
      headers: this.opts.token ? { Authorization: `Bearer ${this.opts.token}` } : {},
    })
    return res.arrayBuffer()
  }

  /** POST /domain/verify（无需鉴权）→ {passed, issues[]} */
  verifyDomainCode(code: string): Promise<{ passed: boolean; issues: string[] }> {
    return this.json('/domain/verify', 'POST', { code }, false) as Promise<{ passed: boolean; issues: string[] }>
  }

  /** GET /prompts/domain（无需鉴权）→ 三段领域人设 */
  getDomainPrompt(): Promise<DomainPrompt> {
    return this.json('/prompts/domain', 'GET', undefined, false) as Promise<DomainPrompt>
  }

  /** GET /knowledge/catalog（无需鉴权）→ 数据集目录 */
  getCatalog(): Promise<Catalog> {
    return this.json('/knowledge/catalog', 'GET', undefined, false) as Promise<Catalog>
  }
}

/** 可中断的 sleep；signal 中止时提前返回，由调用方检查 aborted。 */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms)
    function done() {
      clearTimeout(timer)
      signal?.removeEventListener('abort', done)
      resolve()
    }
    signal?.addEventListener('abort', done, { once: true })
  })
}
