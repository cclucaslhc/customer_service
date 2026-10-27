import { useEffect, useState } from 'react'
import { api } from './api'

type Settings = { enabled: boolean; configured: boolean; target: string | null; revision: number; targets: string[]; policy_configured: boolean; recovery_rebind?: boolean }

export default function StorageSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [target, setTarget] = useState('')
  const [access, setAccess] = useState('')
  const [secret, setSecret] = useState('')
  const [session, setSession] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    void api<Settings>('/api/admin/storage', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]) }).then(value => {
      if (active) { setSettings(value); setTarget(value.target && value.targets.includes(value.target) ? value.target : value.targets[0] || '') }
    }).catch(() => { if (active) setNotice('无法读取对象存储配置，请刷新重试。') })
    return () => { active = false; controller.abort() }
  }, [])
  const save = async () => {
    if (!settings || busy) return
    setBusy(true); setNotice('')
    // No persistence, console output or automatic retry of plaintext credentials.
    const payload = { target, revision: settings.revision, access_key: access, secret_key: secret, session_token: session || null }
    setAccess(''); setSecret(''); setSession('')
    try {
      const next = await api<Settings>('/api/admin/storage', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: AbortSignal.timeout(45000) })
      setSettings(next); setTarget(next.target || '')
      setNotice('凭据已加密保存，私有版本桶检查通过。')
    } catch { setNotice('保存未确认；请刷新核对配置版本后再操作，不会自动重试。') }
    finally { setBusy(false) }
  }
  return <section className="admin-panel">
    <h2>私有对象存储</h2>
    {notice && <p role="status">{notice}</p>}
    {!settings ? <p>正在读取配置…</p> : !settings.enabled || !settings.policy_configured ?
      <p>需要启用 PostgreSQL 登录模式并由部署方配置允许的存储目标。不会自动使用环境密钥。</p> : <>
        <p>当前版本：{settings.revision} · {settings.configured ? '已配置（不会回显凭据）' : '尚未配置'}</p>
        {settings.recovery_rebind && <p role="alert">恢复后待重新绑定：必须选择与恢复对象匹配的批准目标，并显式提供新凭据。不会使用旧桶凭据。</p>}
        <p>只能选择部署批准的目标。首次配置后仅允许轮换凭据；更换桶需要单独迁移现有对象。</p>
        <label>批准的存储目标<select disabled={busy || (settings.configured && !settings.recovery_rebind)} value={target} onChange={e => setTarget(e.target.value)}>
          {settings.targets.map(name => <option key={name} value={name}>{name}</option>)}
        </select></label>
        <label>Access Key<input type="password" autoComplete="off" value={access} disabled={busy} onChange={e => setAccess(e.target.value)} /></label>
        <label>Secret Key<input type="password" autoComplete="new-password" value={secret} disabled={busy} onChange={e => setSecret(e.target.value)} /></label>
        <label>Session Token（可选）<input type="password" autoComplete="off" value={session} disabled={busy} onChange={e => setSession(e.target.value)} /></label>
        <button type="button" disabled={busy || !target || !access || !secret} onClick={() => void save()}>{busy ? '正在检查并保存…' : '检查私有桶并保存凭据'}</button>
      </>}
  </section>
}
