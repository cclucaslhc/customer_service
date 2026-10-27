import { afterEach, describe, expect, it, vi } from 'vitest'
import { requestStop, type ChatOperation } from './chat-control'
import { configureAuth } from './api'

afterEach(() => { configureAuth(null); vi.unstubAllGlobals() })
const operation = (): ChatOperation => ({controller:new AbortController(),sessionId:'synthetic',runId:'',requestId:'',stopRequested:false,cancelInFlight:false,durable:true})
describe('server-confirmed stop', () => {
  it('holds an early stop intent until acknowledgement and does not abort transport', async () => {
    const op = operation(), status = vi.fn(), fetcher = vi.fn().mockResolvedValue(new Response('{"cancelled":true}'))
    vi.stubGlobal('fetch',fetcher)
    await requestStop(op, () => true, status)
    expect(op.stopRequested).toBe(true)
    expect(fetcher).not.toHaveBeenCalled()
    expect(op.controller.signal.aborted).toBe(false)
    op.runId = 'acknowledged'
    await requestStop(op, () => true, status)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(status).toHaveBeenLastCalledWith('停止请求已确认，等待运行结束…')
    expect(op.controller.signal.aborted).toBe(false)
  })
  it('does not claim cancellation on network failure and permits retry', async () => {
    const op = operation(), status = vi.fn()
    op.runId = 'synthetic-run'
    vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('offline')))
    await requestStop(op, () => true, status)
    expect(status).toHaveBeenLastCalledWith('停止请求未确认，请重试或刷新查看运行状态')
    expect(op.cancelInFlight).toBe(false)
    expect(op.controller.signal.aborted).toBe(false)
  })
  it('reads authoritative state when completion wins the cancellation race', async () => {
    const op = operation(), status = vi.fn()
    op.runId = 'synthetic-run'
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"cancelled":false}'))
      .mockResolvedValueOnce(new Response('{"run":{"status":"complete"}}'))
    vi.stubGlobal('fetch',fetcher)
    await requestStop(op, () => true, status)
    expect(status).toHaveBeenLastCalledWith('回答已完成，正在同步结果…')
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(op.controller.signal.aborted).toBe(false)
  })
  it('does not let a late cancellation response overwrite a newer operation', async () => {
    const op = operation(), status = vi.fn()
    op.runId = 'synthetic-run'
    let current = true, resolve!: (response:Response) => void
    vi.stubGlobal('fetch',vi.fn().mockImplementation(() => new Promise<Response>(done => {resolve=done})))
    const pending = requestStop(op, () => current, status)
    current = false; resolve(new Response('{"cancelled":true}')); await pending
    expect(status).toHaveBeenCalledTimes(1)
    expect(status).toHaveBeenLastCalledWith('正在请求停止…')
  })
})
