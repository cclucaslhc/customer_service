export class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function authEnabled(config: unknown): boolean {
  if (!config || typeof config !== 'object' || !('enabled' in config) || typeof config.enabled !== 'boolean') {
    throw new ApiError('认证配置无效', 503)
  }
  return config.enabled
}

let csrfToken: string | null = null
let authEpoch = 0
export function configureAuth(token: string | null) { csrfToken = token; authEpoch += 1 }
function expired() { if (typeof window !== 'undefined') window.dispatchEvent(new Event('auth-expired')) }
function guardEpoch(epoch: number) { if (epoch !== authEpoch) throw new ApiError('身份已变更，请重新操作', 409) }
async function authenticatedFetch(path: string, options: RequestInit = {}) {
  if (!path.startsWith('/api/')) throw new ApiError('仅允许同源 API', 400)
  const epoch = authEpoch
  const headers = new Headers(options.headers)
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  guardEpoch(epoch)
  if (response.status === 401) expired()
  return { response, epoch }
}
export const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const { response, epoch } = await authenticatedFetch(path, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: '请求失败' }))
    // 401 deliberately emits auth-expired, whose listener clears credentials
    // and advances the epoch. Preserve that 401 instead of misclassifying it
    // as a late response from another identity.
    if (response.status !== 401) guardEpoch(epoch)
    throw new ApiError(typeof body.detail === 'string' ? body.detail : '请求失败', response.status,
      typeof body.code === 'string' ? body.code : undefined)
  }
  const body = await response.json()
  guardEpoch(epoch)
  return body
}

export type StreamEvent = { event: string; data: Record<string, unknown>; id?: number }
export type RunSnapshot = { run_id: string; status: string; cursor: number; draft: string; citations: unknown[];
  cancel_requested: boolean; error_code: string | null; message_id: string | null; contact_summary: string | null; event_floor?: number }
const validRunId = (id: unknown): id is string => typeof id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
const eventsPath = (sid: string, rid: string) => `/api/chat/sessions/${encodeURIComponent(sid)}/runs/${rid}/events`

async function retryDelay(signal: AbortSignal, retries: number) {
  if (signal.aborted) throw new DOMException('连接已终止', 'AbortError')
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('连接已终止', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 200 * 2 ** (retries - 1))
    signal.addEventListener('abort', abort, { once: true })
  })
}

export async function* resumeChat(sessionId: string, run: RunSnapshot, signal: AbortSignal): AsyncGenerator<StreamEvent> {
  if (!validRunId(run.run_id) || !Number.isSafeInteger(run.cursor) || run.cursor < 0) throw new ApiError('运行快照无效', 502)
  yield* readChatEvents(null, authEpoch, eventsPath(sessionId, run.run_id), signal, run.cursor)
}

export async function* streamChat(sessionId: string, content: string, signal: AbortSignal): AsyncGenerator<StreamEvent> {
  const requestId = crypto.randomUUID()
  yield {event:'submission_started',data:{request_id:requestId}}
  if (signal.aborted) throw new DOMException('消息未提交', 'AbortError')
  const { response, epoch } = await authenticatedFetch(`/api/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, request_id: requestId }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  })
  if (!response.ok || !response.body) throw new ApiError('无法连接客服服务', response.status)
  let durableUrl = ''
  if (response.headers.get('content-type')?.includes('application/json')) {
    const queued = await response.json()
    guardEpoch(epoch)
    if (!validRunId(queued.run_id)) throw new ApiError('运行编号无效', 502)
    durableUrl = eventsPath(sessionId, queued.run_id)
    // The command acknowledgement arrives before the subscription. A pending
    // Stop can now commit cancellation even if GET events is unavailable.
    yield {event:'run_started',data:{run_id:queued.run_id,execution_mode:'queued'}}
  }
  yield* readChatEvents(response, epoch, durableUrl, signal)
}

async function* readChatEvents(initial: Response | null, epoch: number, durableUrl: string, signal: AbortSignal, cursor = 0): AsyncGenerator<StreamEvent> {
  let retries = 0
  while (true) {
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  try {
    let current = initial
    if (durableUrl) {
      const fetched = await authenticatedFetch(`${durableUrl}?after=${cursor}`, {signal})
      guardEpoch(epoch)
      current = fetched.response
      if (current.status === 409) {
        const failure = await current.json().catch(() => ({}))
        guardEpoch(epoch)
        if (failure.code === 'cursor_expired') { yield* recoverTerminal(durableUrl,cursor,epoch,signal); return }
      }
      if (!current.ok || !current.body) throw new ApiError('无法读取运行事件', current.status)
    }
    reader = current!.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let observed = false
    while (true) {
    const { value, done } = await reader.read()
    if (done) break
    guardEpoch(epoch)
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      let event = 'message'
      let data = '{}'
      let id: number | undefined
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) data = line.slice(5).trim()
        if (line.startsWith('id:')) id = Number(line.slice(3).trim())
      }
      const parsed = JSON.parse(data)
      if (event === 'cursor_expired' && durableUrl) { yield* recoverTerminal(durableUrl,cursor,epoch,signal); return }
      if (event === 'error' && parsed.code === 'authentication_required') { expired(); throw new ApiError('登录已失效，请重新登录', 401) }
      if (id !== undefined && (!Number.isSafeInteger(id) || id < 1)) throw new ApiError('事件编号无效', 502)
      if (id !== undefined && id <= cursor) continue
      if (id !== undefined) cursor = id
      if (id !== undefined) observed = true
      yield { event, data: parsed, ...(id !== undefined ? {id} : {}) }
      if (['done','interrupted','error'].includes(event)) return
    }
    }
    if (!durableUrl) return
    if (!observed && cursor > 0) { yield* recoverTerminal(durableUrl,cursor,epoch,signal); return }
    throw new Error('运行事件连接提前断开')
  } catch (failure) {
    guardEpoch(epoch)
    if (!durableUrl || signal.aborted || retries++ >= 3 || (failure instanceof ApiError && failure.status < 500)) throw failure
    // Only GET events is retried. Never resubmit the model command on disconnect.
    await retryDelay(signal, retries)
  } finally { if (reader) { await reader.cancel().catch(() => undefined); reader.releaseLock() } }
  }
}

async function* recoverTerminal(durableUrl: string, cursor: number, epoch: number, signal: AbortSignal): AsyncGenerator<StreamEvent> {
  // Derive the same-origin route from our own events URL, never from a server-
  // supplied snapshot_url. Reauthenticate before returning any retained state.
  const path = durableUrl.replace(/\/events$/, '')
  const {response} = await authenticatedFetch(path,{signal})
  guardEpoch(epoch)
  if (!response.ok) throw new ApiError('无法读取运行快照',response.status)
  const detail = await response.json()
  guardEpoch(epoch)
  const run = detail.run as RunSnapshot
  if (!run || run.run_id !== path.split('/').at(-1) || !Number.isSafeInteger(run.cursor) || run.cursor < cursor
    || !['complete','error','interrupted'].includes(run.status) || !Array.isArray(detail.messages)) throw new ApiError('终态快照无效',502)
  if (run.status === 'complete' && (!run.message_id || !detail.messages.some((message: {id?:string; role?:string}) =>
    message.id === run.message_id && message.role === 'assistant'))) throw new ApiError('终态消息缺失',502)
  yield {event:'run_snapshot',data:detail}
  if (run.status === 'complete') {
    if (run.contact_summary !== null) yield {event:'contact_form',data:{summary:run.contact_summary}}
    yield {event:'done',data:{run_id:run.run_id,message_id:run.message_id}}
  } else if (run.status === 'error' || run.error_code === 'identity_revoked') {
    yield {event:'error',data:{run_id:run.run_id,code:run.error_code === 'identity_revoked' ? 'run_identity_revoked' : run.error_code,
      message:'运行未完成，请重新登录或稍后重试'}}
  } else yield {event:'interrupted',data:{run_id:run.run_id,reason:run.error_code}}
}
