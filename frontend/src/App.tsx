import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { api, streamChat } from './api'

type Citation = { document: string; section?: string; excerpt: string }
type Message = { role: 'user' | 'assistant'; content: string; citations?: Citation[]; status?: string }
type Provider = { provider: 'deepseek' | 'openai'; model: string; configured: boolean; last_test_status?: string; last_test_message?: string }
type DocumentRow = { id: string; display_name: string; size: number; chunk_count: number; status: string }
type Contact = { id: string; name: string; phone?: string; email?: string; summary: string; created_at: string }

const starterQuestions = ['你们的退款政策是什么？', '查询订单 A10001，手机号后四位 1234', '今天是星期几？']

export default function App() {
  const [page, setPage] = useState<'chat' | 'admin'>('chat')
  const [sessionId, setSessionId] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [runId, setRunId] = useState('')
  const [status, setStatus] = useState('就绪')
  const [showContact, setShowContact] = useState(false)
  const [contactSummary, setContactSummary] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const newSession = async () => {
    const result = await api<{ id: string }>('/api/chat/sessions', { method: 'POST' })
    setSessionId(result.id); setMessages([]); setShowContact(false); setStatus('新会话已创建')
  }
  useEffect(() => { void newSession() }, [])

  const send = async (preset?: string) => {
    const content = (preset ?? input).trim()
    if (!content || running || !sessionId) return
    setInput(''); setMessages(old => [...old, { role: 'user', content }, { role: 'assistant', content: '' }]); setRunning(true); setStatus('正在连接模型…')
    const controller = new AbortController(); abortRef.current = controller
    let citations: Citation[] = []
    try {
      for await (const frame of streamChat(sessionId, content, controller.signal)) {
        if (frame.event === 'run_started') setRunId(String(frame.data.run_id || ''))
        if (frame.event === 'provider') setStatus(`正在使用 ${frame.data.provider} / ${frame.data.model}`)
        if (frame.event === 'token') setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, content: m.content + String(frame.data.text || '') } : m))
        if (frame.event === 'citation') { citations = [...citations, frame.data as unknown as Citation]; setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, citations } : m)) }
        if (frame.event === 'contact_form') { setContactSummary(String(frame.data.summary || content)); setShowContact(true) }
        if (frame.event === 'error') throw new Error(String(frame.data.message || '模型调用失败'))
        if (frame.event === 'done') setStatus('回答完成')
      }
    } catch (error) {
      const message = error instanceof Error && error.name !== 'AbortError' ? error.message : '回答已停止'
      setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, content: m.content || message, status: 'error' } : m)); setStatus(message)
    } finally { setRunning(false); setRunId(''); abortRef.current = null }
  }

  const stop = async () => {
    if (runId) await api(`/api/chat/sessions/${sessionId}/runs/${runId}/cancel`, { method: 'POST' }).catch(() => null)
    abortRef.current?.abort(); setRunning(false); setStatus('回答已停止')
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">云</span><div><strong>云小智</strong><small>AI CUSTOMER CARE</small></div></div>
      <nav>
        <button className={page === 'chat' ? 'active' : ''} onClick={() => setPage('chat')}>对话工作台</button>
        <button className={page === 'admin' ? 'active' : ''} onClick={() => setPage('admin')}>系统管理</button>
      </nav>
      <div className="sidebar-note"><i />本地知识库<br/><span>文档不会生成向量</span></div>
    </aside>
    <main className="main-panel">
      <header><div><h1>{page === 'chat' ? '在线客服' : '系统管理'}</h1><p>{page === 'chat' ? '基于企业知识与业务工具提供可信回答' : '配置模型、知识文档和联系登记'}</p></div>{page === 'chat' && <button className="secondary" onClick={newSession}>＋ 新会话</button>}</header>
      {page === 'chat' ? <section className="chat-layout">
        <div className="conversation">
          {messages.length === 0 && <div className="welcome"><span className="eyebrow">CUSTOMER INTELLIGENCE</span><h2>您好，我是云小智</h2><p>我可以回答公司知识、查询模拟订单和物流，并在需要时登记您的联系方式。</p><div className="starter-grid">{starterQuestions.map(q => <button key={q} onClick={() => void send(q)}>{q}<b>↗</b></button>)}</div></div>}
          {messages.map((message, index) => <div className={`message ${message.role}`} key={index}><div className="avatar">{message.role === 'user' ? '您' : '云'}</div><div className="bubble"><ReactMarkdown>{message.content || '思考中…'}</ReactMarkdown>{message.citations?.length ? <div className="citations"><strong>参考资料</strong>{message.citations.map((c, i) => <details key={i}><summary>{c.document} · {c.section || '正文'}</summary><p>{c.excerpt}</p></details>)}</div> : null}</div></div>)}
        </div>
        <div className="composer"><div className="status-line"><i className={running ? 'pulse' : ''}/>{status}</div><div className="input-row"><textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }} placeholder="请输入您的问题，Enter 发送…" />{running ? <button className="stop" onClick={stop}>停止</button> : <button className="send" onClick={() => void send()}>发送</button>}</div></div>
        {showContact && <ContactForm sessionId={sessionId} summary={contactSummary} onClose={() => setShowContact(false)} />}
      </section> : <Admin />}
    </main>
  </div>
}

function ContactForm({ sessionId, summary, onClose }: { sessionId: string; summary: string; onClose: () => void }) {
  const [form, setForm] = useState({ name: '', phone: '', email: '', summary })
  const [saved, setSaved] = useState(false)
  const submit = async () => { await api('/api/contacts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, session_id: sessionId, phone: form.phone || null, email: form.email || null }) }); setSaved(true) }
  return <div className="modal-backdrop"><div className="modal"><button className="close" onClick={onClose}>×</button>{saved ? <div className="success"><b>✓</b><h3>联系方式已登记</h3><p>这是本地演示记录，不会创建真实工单。</p></div> : <><span className="eyebrow">CONTACT REQUEST</span><h3>登记联系方式</h3><input placeholder="您的称呼" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}/><div className="split"><input placeholder="手机号" value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })}/><input placeholder="邮箱" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })}/></div><textarea value={form.summary} onChange={e => setForm({ ...form, summary: e.target.value })}/><button className="primary" onClick={() => void submit()}>提交登记</button></>}</div></div>
}

function Admin() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [active, setActive] = useState('deepseek')
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [models, setModels] = useState<Record<string, string>>({})
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [contacts, setContacts] = useState<Contact[]>([])
  const [notice, setNotice] = useState('')
  const load = async () => {
    const config = await api<{ active_provider: string; providers: Provider[] }>('/api/admin/providers'); setProviders(config.providers); setActive(config.active_provider); setModels(Object.fromEntries(config.providers.map(p => [p.provider, p.model])))
    setDocuments(await api('/api/admin/documents')); setContacts(await api('/api/admin/contacts'))
  }
  useEffect(() => { void load() }, [])
  const save = async (provider: string) => { await api(`/api/admin/providers/${provider}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: models[provider], api_key: keys[provider] || null }) }); setKeys({ ...keys, [provider]: '' }); setNotice(`${provider} 配置已安全保存`); await load() }
  const test = async (provider: string) => { const result = await api<{ message: string }>(`/api/admin/providers/${provider}/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: models[provider], api_key: keys[provider] || null }) }); setNotice(result.message); await load() }
  const activate = async (provider: string) => { await api('/api/admin/active-provider', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider }) }); setNotice('已切换，下一条消息生效'); await load() }
  const upload = async (file?: File) => { if (!file) return; const body = new FormData(); body.append('file', file); await api('/api/admin/documents', { method: 'POST', body }); setNotice('文档已解析并建立全文索引'); await load() }
  const removeDoc = async (id: string) => { await api(`/api/admin/documents/${id}`, { method: 'DELETE' }); await load() }
  return <div className="admin-grid">
    {notice && <div className="notice">{notice}</div>}
    <section className="card wide"><div className="card-title"><div><span className="eyebrow">MODEL GATEWAY</span><h2>在线模型</h2></div><span className="badge">当前：{active}</span></div><p className="muted">密钥在浏览器中只写，后端使用本地主密钥加密保存，不会再次回显。</p><div className="provider-grid">{providers.map(p => <div className={`provider-card ${active === p.provider ? 'selected' : ''}`} key={p.provider}><div className="provider-head"><div><strong>{p.provider === 'deepseek' ? 'DeepSeek' : 'OpenAI'}</strong><small>{p.configured ? '密钥已配置' : '尚未配置'}</small></div><i className={p.configured ? 'ok' : ''}/></div><label>模型名<input value={models[p.provider] || ''} onChange={e => setModels({ ...models, [p.provider]: e.target.value })}/></label><label>API Key<input type="password" autoComplete="new-password" value={keys[p.provider] || ''} onChange={e => setKeys({ ...keys, [p.provider]: e.target.value })} placeholder={p.configured ? '留空表示不修改' : '在此录入，仅发送给本机后端'}/></label><div className="actions"><button onClick={() => void test(p.provider)}>测试连接</button><button onClick={() => void save(p.provider)}>保存</button><button className="primary" disabled={!p.configured || active === p.provider} onClick={() => void activate(p.provider)}>设为当前</button></div>{p.last_test_message && <small className="test-result">上次测试：{p.last_test_message}</small>}</div>)}</div></section>
    <section className="card"><div className="card-title"><div><span className="eyebrow">LOCAL KNOWLEDGE</span><h2>知识文档</h2></div><label className="upload">上传文件<input type="file" accept=".pdf,.docx,.md,.txt" onChange={e => void upload(e.target.files?.[0])}/></label></div><div className="list">{documents.length ? documents.map(d => <div className="list-row" key={d.id}><div><strong>{d.display_name}</strong><small>{(d.size / 1024).toFixed(1)} KB · {d.chunk_count} 个片段</small></div><button className="danger" onClick={() => void removeDoc(d.id)}>删除</button></div>) : <div className="empty">还没有知识文档</div>}</div></section>
    <section className="card"><div className="card-title"><div><span className="eyebrow">CONTACT LOG</span><h2>联系登记</h2></div><span className="badge">{contacts.length}</span></div><div className="list">{contacts.length ? contacts.map(c => <div className="list-row contact" key={c.id}><div><strong>{c.name}</strong><small>{c.phone || c.email}</small><p>{c.summary}</p></div></div>) : <div className="empty">暂无登记记录</div>}</div></section>
  </div>
}
