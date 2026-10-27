import { useEffect, useState } from 'react'
import { api, ApiError, authEnabled, configureAuth } from './api'
import App from './App'
import './auth.css'
import { isRecoveryHold, recoveryHoldMessage } from './auth-errors'

type Principal = { user_id: string; tenant_id: string; role: 'customer' | 'support' | 'admin' }
type Identity = { principal: Principal; csrf_token: string }

export default function AuthBoundary() {
  const [checking, setChecking] = useState(true)
  const [enabled, setEnabled] = useState(true)
  const [identity, setIdentity] = useState<Principal | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [held, setHeld] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [tenant, setTenant] = useState('')
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    let active = true
    const clear = () => { configureAuth(null); setIdentity(null); setPassword(''); setUsername(''); setTenant(''); setGeneration(n => n + 1); window.history.replaceState({}, '', '/') }
    window.addEventListener('auth-expired', clear)
    void (async () => {
      try {
        const config = await api<unknown>('/api/auth/config')
        const authRequired = authEnabled(config)
        if (!active) return
        setEnabled(authRequired)
        if (authRequired) {
          try {
            const result = await api<Identity>('/api/auth/me')
            if (active) { configureAuth(result.csrf_token); setIdentity(result.principal) }
          } catch (failure) { if (!(failure instanceof ApiError && failure.status === 401)) throw failure }
        }
      } catch (failure) { if (active) {
        if (isRecoveryHold(failure)) { configureAuth(null); setIdentity(null); setHeld(true) }
        else setError('无法确认认证状态，请刷新重试。不会降级为未认证模式。')
      } }
      finally { if (active) setChecking(false) }
    })()
    return () => { active = false; window.removeEventListener('auth-expired', clear) }
  }, [])
  useEffect(() => {
    if (!identity) return
    const check = () => { void api<Identity>('/api/auth/me').catch(failure => {
      if (isRecoveryHold(failure)) { configureAuth(null); setIdentity(null); setPassword(''); setHeld(true) }
    }) }
    const timer = window.setInterval(check, 30000)
    window.addEventListener('focus', check)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', check) }
  }, [identity])
  const login = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await api<Identity>('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, tenant_id: tenant }) })
      configureAuth(result.csrf_token); setPassword(''); setIdentity(result.principal); setGeneration(n => n + 1)
    } catch (failure) {
      setPassword('')
      if (isRecoveryHold(failure)) { configureAuth(null); setIdentity(null); setHeld(true) }
      else setError(failure instanceof ApiError && failure.status === 429 ? '登录尝试过多，请稍后重试。' : '登录失败，请核对账号、密码和租户，或联系管理员。')
    }
    finally { setBusy(false) }
  }
  const logout = async () => {
    setBusy(true); setError('')
    try {
      await api('/api/auth/logout', { method: 'POST' })
      configureAuth(null); setIdentity(null); setUsername(''); setTenant(''); setGeneration(n => n + 1)
      window.history.replaceState({}, '', '/')
    } catch (failure) {
      if (isRecoveryHold(failure)) { configureAuth(null); setIdentity(null); setHeld(true) }
      else setError('退出尚未确认，请重试。')
    }
    finally { setBusy(false) }
  }
  if (checking) return <main className="auth-screen" role="status">正在确认登录状态…</main>
  if (held) return <main className="auth-screen"><section className="auth-card" role="alert"><h1>服务暂停中</h1><p>{recoveryHoldMessage}</p></section></main>
  if (!enabled) return <App />
  if (identity) return <><div className="auth-bar"><span>已登录 · {identity.role === 'admin' ? '管理员' : identity.role === 'support' ? '客服' : '客户'}</span>{error && <span role="alert">{error}</span>}<button disabled={busy} onClick={() => void logout()}>退出登录</button></div><App key={generation} role={identity.role} /></>
  return <main className="auth-screen"><form className="auth-card" onSubmit={event => void login(event)}><h1>登录客服服务</h1><p>请使用管理员提供的账号和租户编号。没有默认账号，也不开放自行注册。</p>
    <label>账号<input required autoComplete="username" maxLength={64} value={username} onChange={e => setUsername(e.target.value)} /></label>
    <label>密码<input required type="password" autoComplete="current-password" minLength={12} maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /></label>
    <label>租户编号<input required maxLength={36} value={tenant} onChange={e => setTenant(e.target.value)} /></label>
    {error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? '正在登录…' : '登录'}</button></form></main>
}
