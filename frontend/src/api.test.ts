import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, authEnabled, configureAuth, streamChat, resumeChat, type RunSnapshot, type StreamEvent } from './api'

afterEach(() => { configureAuth(null); vi.unstubAllGlobals() })
describe('cookie and CSRF request boundary', () => {
  it('keeps a 401 after its expiry handler deliberately advances the epoch', async () => {
    const events = new EventTarget()
    events.addEventListener('auth-expired', () => configureAuth(null)); vi.stubGlobal('window', events)
    configureAuth('synthetic-csrf')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"Synthetic signed-out identity"}', {status:401})))
    await expect(api('/api/auth/me')).rejects.toMatchObject({status:401})
  })
  it('preserves an explicit recovery hold without treating it as identity expiry', async () => {
    const events = new EventTarget(), expired = vi.fn()
    events.addEventListener('auth-expired', expired); vi.stubGlobal('window', events)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({code:'recovery_hold',detail:'Synthetic held service'}), {status:503})))
    await expect(api('/api/auth/config')).rejects.toMatchObject({status:503,code:'recovery_hold'})
    expect(expired).not.toHaveBeenCalled()
  })
  it('requires an explicit boolean before allowing unauthenticated mode', () => {
    expect(authEnabled({ enabled: false })).toBe(false)
    expect(authEnabled({ enabled: true })).toBe(true)
    for (const config of [null, {}, { enabled: null }, { enabled: 0 }, { enabled: 'false' }]) {
      expect(() => authEnabled(config)).toThrow(ApiError)
    }
  })
  it('adds CSRF only to unsafe methods and uses same-origin cookies', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{}', {status:200}))
    vi.stubGlobal('fetch', fetcher); configureAuth('synthetic-csrf')
    await api('/api/test', {method:'POST', headers:{'Content-Type':'application/json'}})
    expect(fetcher.mock.calls[0][1].credentials).toBe('same-origin')
    expect(fetcher.mock.calls[0][1].headers.get('X-CSRF-Token')).toBe('synthetic-csrf')
    fetcher.mockResolvedValueOnce(new Response('{}', {status:200}))
    await api('/api/test')
    expect(fetcher.mock.calls[1][1].headers.has('X-CSRF-Token')).toBe(false)
    await expect(api('https://evil.invalid/api')).rejects.toBeInstanceOf(ApiError)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('rejects responses arriving after an identity transition', async () => {
    let resolve!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise<Response>(done => { resolve=done })))
    configureAuth('synthetic-a')
    const pending = api('/api/chat/sessions')
    configureAuth('synthetic-b')
    resolve(new Response('[{"id":"synthetic-private-a"}]'))
    await expect(pending).rejects.toMatchObject({status:409})
  })
  it('dispatches identity expiry on 401 and streamed authentication error', async () => {
    const events = new EventTarget(), expired = vi.fn()
    events.addEventListener('auth-expired', expired); vi.stubGlobal('window', events)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"denied"}', {status:401})))
    await expect(api('/api/auth/me')).rejects.toMatchObject({status:401})
    expect(expired).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('event: error\ndata: {"code":"authentication_required"}\n\n')))
    await expect((async () => { for await (const event of streamChat('synthetic', 'test', new AbortController().signal)) void event })()).rejects.toMatchObject({status:401})
    expect(expired).toHaveBeenCalledTimes(2)
  })
})

describe('durable SSE reconnection', () => {
  const rid = '00000000-0000-4000-8000-000000000001'
  const queued = () => new Response(JSON.stringify({run_id:rid, events_url:'https://evil.invalid/'}), {
    status:202, headers:{'Content-Type':'application/json'},
  })
  const frame = (id:number, event:string, data:unknown) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`
  it('reconnects GET from its cursor, deduplicates events and never resubmits POST', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(queued())
      .mockResolvedValueOnce(new Response(frame(1,'run_started',{run_id:rid})+frame(2,'token',{text:'A'})))
      .mockResolvedValueOnce(new Response(frame(2,'token',{text:'A'})+frame(3,'token',{text:'B'})+frame(4,'done',{run_id:rid})))
    vi.stubGlobal('fetch',fetcher)
    const events = []
    for await (const event of streamChat('synthetic','question',new AbortController().signal)) events.push(event)
    expect(events.filter(event => event.id !== undefined).map(event => event.id)).toEqual([1,2,3,4])
    expect(events[0].event).toBe('submission_started')
    expect(events[1]).toMatchObject({event:'run_started',data:{run_id:rid,execution_mode:'queued'}})
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(fetcher.mock.calls[0][1].method).toBe('POST')
    expect(fetcher.mock.calls[2][0]).toBe(`/api/chat/sessions/synthetic/runs/${rid}/events?after=2`)
    expect(fetcher.mock.calls.slice(1).every(call => !call[1].method)).toBe(true)
  })
  it('bounds failed subscription retries and never retries a failed POST', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(queued()).mockRejectedValue(new TypeError('network unavailable'))
    vi.stubGlobal('fetch',fetcher)
    await expect((async () => { for await (const event of streamChat('synthetic','question',new AbortController().signal)) void event })()).rejects.toThrow('network unavailable')
    expect(fetcher).toHaveBeenCalledTimes(5)
    fetcher.mockReset().mockRejectedValue(new TypeError('POST unavailable'))
    await expect((async () => { for await (const event of streamChat('synthetic','question',new AbortController().signal)) void event })()).rejects.toThrow('POST unavailable')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('does not subscribe after the identity changes while reading a queued response', async () => {
    const response = queued()
    const original = response.json.bind(response)
    vi.spyOn(response,'json').mockImplementation(async () => { configureAuth('new-identity'); return original() })
    const fetcher = vi.fn().mockResolvedValueOnce(response)
    vi.stubGlobal('fetch',fetcher)
    await expect((async () => { for await (const event of streamChat('synthetic','question',new AbortController().signal)) void event })()).rejects.toMatchObject({status:409})
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('resumes from the snapshot cursor without creating another command', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(frame(4,'token',{text:'next'})+frame(5,'done',{run_id:rid})))
    vi.stubGlobal('fetch',fetcher)
    const events = []
    for await (const event of resumeChat('synthetic', {run_id:rid,cursor:3} as RunSnapshot, new AbortController().signal)) events.push(event)
    expect(events.map(event => event.id)).toEqual([4,5])
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/sessions/synthetic/runs/${rid}/events?after=3`)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('exposes command acknowledgement before trying GET, even if events are unavailable', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(queued())
    vi.stubGlobal('fetch',fetcher)
    const stream = streamChat('synthetic','question',new AbortController().signal)
    expect((await stream.next()).value?.event).toBe('submission_started')
    expect(fetcher).not.toHaveBeenCalled()
    expect((await stream.next()).value).toMatchObject({event:'run_started',data:{run_id:rid}})
    expect(fetcher).toHaveBeenCalledTimes(1)
    await stream.return(undefined)
  })
  it('recovers an expired cursor through the derived same-origin snapshot without resubmission', async () => {
    const snapshot = {messages:[{role:'assistant',id:'saved',content:'retained answer'}],run:{
      run_id:rid,status:'complete',cursor:9,message_id:'saved',contact_summary:null}}
    const fetcher = vi.fn().mockResolvedValueOnce(queued())
      .mockResolvedValueOnce(new Response('{"code":"cursor_expired","snapshot_url":"https://evil.invalid/"}',{status:409}))
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot)))
    vi.stubGlobal('fetch',fetcher)
    const events = []
    for await (const event of streamChat('synthetic','question',new AbortController().signal)) events.push(event)
    expect(events.at(-2)).toMatchObject({event:'run_snapshot',data:snapshot})
    expect(events.at(-1)).toMatchObject({event:'done',data:{message_id:'saved'}})
    expect(fetcher.mock.calls[2][0]).toBe(`/api/chat/sessions/synthetic/runs/${rid}`)
    expect(fetcher).toHaveBeenCalledTimes(3)
  })
  it('handles retention overtaking an open stream without appending a second answer', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(queued())
      .mockResolvedValueOnce(new Response(frame(3,'token',{text:'partial'})+'event: cursor_expired\ndata: {}\n\n'))
      .mockResolvedValueOnce(new Response(JSON.stringify({messages:[],run:{run_id:rid,status:'interrupted',cursor:9,error_code:'cancelled'}})))
    vi.stubGlobal('fetch',fetcher)
    const events = []
    for await (const event of streamChat('synthetic','question',new AbortController().signal)) events.push(event)
    expect(events.at(-2)?.event).toBe('run_snapshot')
    expect(events.at(-1)).toMatchObject({event:'interrupted',data:{reason:'cancelled'}})
    expect(fetcher).toHaveBeenCalledTimes(3)
  })
  it('does not expose a retained snapshot after its identity transition', async () => {
    const snapshot = new Response('{}')
    vi.spyOn(snapshot,'json').mockImplementation(async () => { configureAuth('changed'); return {
      messages:[{content:'old private answer'}],run:{run_id:rid,status:'complete',cursor:9}} })
    const fetcher = vi.fn().mockResolvedValueOnce(queued())
      .mockResolvedValueOnce(new Response('{"code":"cursor_expired"}',{status:409})).mockResolvedValueOnce(snapshot)
    vi.stubGlobal('fetch',fetcher)
    const events: StreamEvent[] = []
    await expect((async () => { for await (const event of streamChat('synthetic','question',new AbortController().signal)) events.push(event) })()).rejects.toMatchObject({status:409})
    expect(events.some(event => event.event === 'run_snapshot')).toBe(false)
  })
  it('resolves a drained terminal cursor through its saved message instead of reporting a false disconnect', async () => {
    const snapshot = {messages:[{id:'saved',role:'assistant',content:'final'}],run:{
      run_id:rid,status:'complete',cursor:9,message_id:'saved',contact_summary:null}}
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('')).mockResolvedValueOnce(new Response(JSON.stringify(snapshot)))
    vi.stubGlobal('fetch',fetcher)
    const events = []
    for await (const event of resumeChat('synthetic',{run_id:rid,cursor:9} as RunSnapshot,new AbortController().signal)) events.push(event)
    expect(events.map(event => event.event)).toEqual(['run_snapshot','done'])
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
