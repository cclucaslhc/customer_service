import { api, type RunSnapshot } from './api'

export type ChatOperation = {
  controller: AbortController; sessionId: string; runId: string; requestId: string;
  stopRequested: boolean; cancelInFlight: boolean; durable: boolean;
}

// A transport abort is not a durable cancellation. Keep the subscription alive
// until a terminal event proves termination, or report an unconfirmed request.
export async function requestStop(op: ChatOperation, isCurrent: () => boolean, status: (value: string) => void) {
  op.stopRequested = true
  if (!op.runId) { if (isCurrent()) status('停止已请求，等待消息回执…'); return }
  if (op.cancelInFlight) return
  op.cancelInFlight = true
  if (isCurrent()) status('正在请求停止…')
  try {
    const deadline = AbortSignal.timeout(10000)
    const result = await api<{cancelled:boolean}>(`/api/chat/sessions/${op.sessionId}/runs/${op.runId}/cancel`, {method:'POST',signal:deadline})
    if (!isCurrent()) return
    if (result.cancelled) { status('停止请求已确认，等待运行结束…'); return }
    if (op.durable) {
      const snapshot = await api<{run:RunSnapshot}>(`/api/chat/sessions/${op.sessionId}/runs/${op.runId}`, {signal:deadline})
      if (!isCurrent()) return
      if (snapshot.run.status === 'complete') { status('回答已完成，正在同步结果…'); return }
      if (['interrupted','error'].includes(snapshot.run.status)) { status('运行已结束，正在同步结果…'); return }
    }
    status('停止尚未确认，任务可能仍在运行')
  } catch {
    if (isCurrent()) status('停止请求未确认，请重试或刷新查看运行状态')
  } finally { op.cancelInFlight = false }
}
