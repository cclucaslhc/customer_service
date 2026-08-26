import { useEffect, useRef, useState } from 'react'
import { ApiError, api, streamChat } from './api'
import { adminSessionListPath, messageFeedbackPath, quoteMessageContent, sessionIdFromSearch, sessionListPath, sessionUrl } from './session'
import AdminConsole from './AdminConsole'
import MarkdownContent from './MarkdownContent'

type KnowledgeImage = { id: string; url: string; alt_text: string; location: string; width: number; height: number; media_type: string }
type Citation = { document: string; section?: string; excerpt: string; images?: KnowledgeImage[] }
type Message = { id?: string; role: 'user' | 'assistant'; content: string; citations?: Citation[]; feedback?: 'up' | 'down' | null; status?: string; created_at?: string }
type SessionSummary = { id: string; title: string; status: string; message_count: number; last_message?: string; archived_at?: string; deleted_at?: string; created_at: string; updated_at: string }
type SessionDetail = { session: SessionSummary; messages: Message[] }
type AdminSession = SessionSummary & { run_count: number; error_count: number; last_error_code?: string; provider?: string; model?: string; contact_count: number }
type Provider = { provider: 'deepseek' | 'openai'; model: string; configured: boolean; last_test_status?: string; last_test_message?: string }
type DocumentRow = { id: string; display_name: string; size: number; chunk_count: number; status: string }
type Contact = { id: string; name: string; phone?: string; email?: string; summary: string; created_at: string }
type KnowledgeLocation = { path: string; supported_extensions: string[] }
type ImportIssue = { path: string; display_name?: string; reason: string }
type ImportDetail = ImportIssue & { kind: '跳过' | '失败' }
type KnowledgeImportResult = { imported_count: number; skipped_count: number; failed_count: number; skipped: ImportIssue[]; failed: ImportIssue[] }

const starterQuestions = ['你们的退款政策是什么？', '查询订单 A10001，手机号后四位 1234', '今天是星期几？']

export default function App() {
  const [page, setPage] = useState<'chat' | 'admin'>(() => window.location.pathname.startsWith('/admin') ? 'admin' : 'chat')
  const [sessionId, setSessionId] = useState('')
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionScope, setSessionScope] = useState<'active' | 'archived'>('active')
  const [editingSession, setEditingSession] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [deleteConfirm, setDeleteConfirm] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [quotedMessage, setQuotedMessage] = useState<Message | null>(null)
  const [toast, setToast] = useState('')
  const [running, setRunning] = useState(false)
  const [runId, setRunId] = useState('')
  const [status, setStatus] = useState('就绪')
  const [showContact, setShowContact] = useState(false)
  const [contactSummary, setContactSummary] = useState('')
  const [loadingSession, setLoadingSession] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const composerRef = useRef<HTMLTextAreaElement | null>(null)
  const toastTimerRef = useRef<number | null>(null)
  const showToast = (message: string) => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current)
    setToast(message)
    toastTimerRef.current = window.setTimeout(() => { setToast(''); toastTimerRef.current = null }, 1600)
  }
  const navigatePage = (target: 'chat' | 'admin') => {
    setPage(target)
    window.history.pushState({}, '', target === 'admin' ? '/admin/overview' : '/')
  }

  const updateSessionUrl = (id: string, mode: 'push' | 'replace' = 'push') => {
    window.history[mode === 'push' ? 'pushState' : 'replaceState']({}, '', sessionUrl(window.location.href, id))
  }
  const refreshSessions = async () => {
    setSessions(await api<SessionSummary[]>(sessionListPath(sessionScope)))
  }
  const openSession = async (id: string, updateUrl = true) => {
    if (abortRef.current) return
    setLoadingSession(true)
    setQuotedMessage(null)
    try {
      const detail = await api<SessionDetail>(`/api/chat/sessions/${id}`)
      setSessionId(id); setMessages(detail.messages); setQuotedMessage(null); setShowContact(false); setStatus('历史会话已加载')
      if (updateUrl) updateSessionUrl(id)
    } catch (error) {
      setSessionId(''); setMessages([]); updateSessionUrl('', 'replace')
      setStatus(error instanceof Error ? error.message : '会话加载失败')
    } finally { setLoadingSession(false) }
  }
  const newSession = () => {
    if (abortRef.current) return
    setSessionId(''); setMessages([]); setInput(''); setQuotedMessage(null); setShowContact(false); setStatus('新对话将在发送第一条消息时创建'); updateSessionUrl('')
  }
  const resetCurrentSession = (id: string, message: string) => {
    if (sessionId === id) { setSessionId(''); setMessages([]); setQuotedMessage(null); setShowContact(false); updateSessionUrl('', 'replace') }
    setStatus(message)
  }
  const renameSession = async (id: string) => {
    const title = editingTitle.trim()
    if (!title) return
    await api(`/api/chat/sessions/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) })
    setEditingSession(''); setEditingTitle(''); await refreshSessions()
  }
  const archiveSession = async (id: string) => {
    await api(`/api/chat/sessions/${id}/archive`, { method: 'POST' }); resetCurrentSession(id, '会话已归档，可在“已归档”中恢复'); await refreshSessions()
  }
  const restoreSession = async (id: string) => {
    await api(`/api/chat/sessions/${id}/restore`, { method: 'POST' }); setStatus('会话已恢复'); await refreshSessions()
  }
  const deleteSession = async (id: string) => {
    await api(`/api/chat/sessions/${id}`, { method: 'DELETE' }); setDeleteConfirm(''); resetCurrentSession(id, '会话已安全删除，管理员仍可恢复'); await refreshSessions()
  }
  useEffect(() => {
    const restore = () => {
      setPage(window.location.pathname.startsWith('/admin') ? 'admin' : 'chat')
      void refreshSessions()
      const id = sessionIdFromSearch(window.location.search)
      if (id) void openSession(id, false)
      else { setSessionId(''); setMessages([]); setQuotedMessage(null); setStatus('新对话将在发送第一条消息时创建') }
    }
    restore()
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])
  useEffect(() => { void refreshSessions() }, [sessionScope])
  useEffect(() => () => { if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current) }, [])

  const send = async (preset?: string) => {
    const typedContent = (preset ?? input).trim()
    if (!typedContent || running) return
    const selectedQuote = preset ? null : quotedMessage
    const content = selectedQuote ? `${quoteMessageContent(selectedQuote.role, selectedQuote.content)}${typedContent}` : typedContent
    setRunning(true)
    let activeSessionId = sessionId
    let messageAdded = false
    let citations: Citation[] = []
    try {
      if (!activeSessionId) {
        setStatus('正在创建会话…')
        const created = await api<{ id: string }>('/api/chat/sessions', { method: 'POST' })
        activeSessionId = created.id; setSessionId(created.id); updateSessionUrl(created.id, 'replace')
      }
      setInput(''); setQuotedMessage(null); setMessages(old => [...old, { role: 'user', content }, { role: 'assistant', content: '' }]); setStatus('正在连接模型…'); messageAdded = true
      const controller = new AbortController(); abortRef.current = controller
      for await (const frame of streamChat(activeSessionId, content, controller.signal)) {
        if (frame.event === 'run_started') setRunId(String(frame.data.run_id || ''))
        if (frame.event === 'provider') setStatus(`正在使用 ${frame.data.provider} / ${frame.data.model}`)
        if (frame.event === 'answer_reset') setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, content: '' } : m))
        if (frame.event === 'token') setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, content: m.content + String(frame.data.text || '') } : m))
        if (frame.event === 'tool_start') setStatus(frame.data.tool === 'search_knowledge' ? '正在检索企业知识库…' : '正在查询业务信息…')
        if (frame.event === 'tool_end') setStatus('查询完成，正在组织回答…')
        if (frame.event === 'citation') { citations = [...citations, frame.data as unknown as Citation]; setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, citations } : m)) }
        if (frame.event === 'contact_form') { setContactSummary(String(frame.data.summary || content)); setShowContact(true) }
        if (frame.event === 'error') throw new Error(String(frame.data.message || '模型调用失败'))
        if (frame.event === 'done') {
          const messageId = String(frame.data.message_id || '')
          if (messageId) setMessages(old => old.map((message, index) => index === old.length - 1 ? { ...message, id: messageId } : message))
          setStatus('回答完成')
        }
      }
    } catch (error) {
      const message = error instanceof Error && error.name !== 'AbortError' ? error.message : '回答已停止'
      if (messageAdded) setMessages(old => old.map((m, i) => i === old.length - 1 ? { ...m, content: m.content || message, status: 'error' } : m))
      setStatus(message)
    } finally { setRunning(false); setRunId(''); abortRef.current = null; await refreshSessions().catch(() => null) }
  }

  const stop = async () => {
    if (runId) await api(`/api/chat/sessions/${sessionId}/runs/${runId}/cancel`, { method: 'POST' }).catch(() => null)
    abortRef.current?.abort(); setRunning(false); setStatus('回答已停止')
  }

  const copyMessage = async (message: Message) => {
    try {
      await navigator.clipboard.writeText(message.content)
      showToast('复制成功')
    } catch {
      showToast('复制失败，请检查剪贴板权限')
    }
  }

  const quoteMessage = (message: Message) => {
    setQuotedMessage(message)
    setStatus('已引用消息，可继续编辑后发送')
    requestAnimationFrame(() => composerRef.current?.focus())
  }

  const rateMessage = async (message: Message, rating: 'up' | 'down') => {
    if (!message.id || !sessionId) return
    const previous = message.feedback || null
    const next = previous === rating ? null : rating
    setMessages(old => old.map(item => item.id === message.id ? { ...item, feedback: next } : item))
    try {
      await api(messageFeedbackPath(sessionId, message.id), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating: next }),
      })
      setStatus(next === 'up' ? '感谢您的认可' : next === 'down' ? '评价已记录，我们会继续改进' : '已取消评价')
    } catch (error) {
      setMessages(old => old.map(item => item.id === message.id ? { ...item, feedback: previous } : item))
      setStatus(error instanceof Error ? error.message : '评价保存失败')
    }
  }

  return <div className={`app-shell page-${page}`}>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">云</span><div><strong>云小智</strong><small>AI CUSTOMER CARE</small></div></div>
      <nav>
        <button className={page === 'chat' ? 'active' : ''} onClick={() => navigatePage('chat')}>对话工作台</button>
        <button className={page === 'admin' ? 'active' : ''} onClick={() => navigatePage('admin')}>系统管理</button>
      </nav>
      <div className="sidebar-note"><i />本地知识库<br/><span>文档不会生成向量</span></div>
    </aside>
    <main className={`main-panel ${page === 'admin' ? 'admin-mode' : ''}`}>
      <header className="chat-header"><div><h1>{page === 'chat' ? '在线客服' : '系统管理'}</h1><p>{page === 'chat' ? '基于企业知识与业务工具提供可信回答' : '配置模型、知识文档和联系登记'}</p></div>{page === 'chat' && <button className="secondary" disabled={running} onClick={newSession}>＋ 新会话</button>}</header>
      {page === 'chat' ? <section className="chat-layout">
        <aside className="session-panel">
          <div className="session-panel-head"><div><span className="eyebrow">CONVERSATIONS</span><strong>历史会话</strong></div><span>{sessions.length}</span></div>
          <div className="session-tabs"><button className={sessionScope === 'active' ? 'active' : ''} onClick={() => setSessionScope('active')}>当前</button><button className={sessionScope === 'archived' ? 'active' : ''} onClick={() => setSessionScope('archived')}>已归档</button></div>
          <div className="session-list">{sessions.length ? sessions.map(item => <div className={`session-item ${sessionId === item.id ? 'active' : ''}`} key={item.id}>
            {editingSession === item.id ? <div className="session-edit"><input autoFocus maxLength={80} value={editingTitle} onChange={e => setEditingTitle(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void renameSession(item.id); if (e.key === 'Escape') setEditingSession('') }}/><button onClick={() => void renameSession(item.id)}>保存</button><button onClick={() => setEditingSession('')}>取消</button></div> : <button className="session-open" disabled={running || loadingSession} onClick={() => void openSession(item.id)}><strong>{item.title}</strong><span>{item.last_message || '暂无内容'}</span><small>{new Date(item.updated_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · {item.message_count} 条</small></button>}
            <div className="session-actions">{sessionScope === 'active' ? <><button onClick={() => { setEditingSession(item.id); setEditingTitle(item.title) }}>重命名</button><button onClick={() => void archiveSession(item.id)}>归档</button></> : <button onClick={() => void restoreSession(item.id)}>恢复</button>}{deleteConfirm === item.id ? <><span>确认删除？</span><button className="danger" onClick={() => void deleteSession(item.id)}>确认</button><button onClick={() => setDeleteConfirm('')}>取消</button></> : <button className="danger" onClick={() => setDeleteConfirm(item.id)}>删除</button>}</div>
          </div>) : <div className="session-empty">{sessionScope === 'active' ? '发送第一条消息后，会话会出现在这里。' : '暂无已归档会话。'}</div>}</div>
        </aside>
        <div className="chat-stage">
          <div className="conversation">
            {loadingSession && <div className="history-loading">正在加载历史会话…</div>}
            {!loadingSession && messages.length === 0 && <div className="welcome"><span className="eyebrow">CUSTOMER INTELLIGENCE</span><h2>您好，我是云小智</h2><p>我可以回答公司知识、查询模拟订单和物流，并在需要时登记您的联系方式。</p><div className="starter-grid">{starterQuestions.map(q => <button key={q} onClick={() => void send(q)}>{q}<b>↗</b></button>)}</div></div>}
            {messages.map((message, index) => <div className={`message ${message.role}`} key={message.id || index}><div className="avatar">{message.role === 'user' ? '您' : '云'}</div><div className="bubble"><MarkdownContent>{message.content || '思考中…'}</MarkdownContent>{message.citations?.length ? <div className="citations"><strong>参考资料</strong>{message.citations.map((c, i) => <details key={i}><summary>{c.document} · {c.section || '正文'}</summary><p>{c.excerpt}</p>{c.images?.length ? <KnowledgeImages images={c.images} /> : null}</details>)}</div> : null}{message.content && <div className="message-actions"><button title="复制消息" aria-label="复制消息" onClick={() => void copyMessage(message)}>复制</button><button title="引用消息" aria-label="引用消息" onClick={() => quoteMessage(message)}>引用</button>{message.role === 'assistant' && message.id && message.status !== 'error' && <><button className={message.feedback === 'up' ? 'selected' : ''} title="回答有帮助" aria-label="回答有帮助" aria-pressed={message.feedback === 'up'} onClick={() => void rateMessage(message, 'up')}>👍</button><button className={message.feedback === 'down' ? 'selected' : ''} title="回答需改进" aria-label="回答需改进" aria-pressed={message.feedback === 'down'} onClick={() => void rateMessage(message, 'down')}>👎</button></>}</div>}</div></div>)}
          </div>
          <div className="composer">{quotedMessage && <div className="quote-preview"><div><strong>引用{quotedMessage.role === 'user' ? '我的消息' : ' AI 客服回答'}</strong><span>{quotedMessage.content.replace(/\s+/g, ' ').slice(0, 180)}{quotedMessage.content.replace(/\s+/g, ' ').length > 180 ? '…' : ''}</span></div><button aria-label="取消引用" title="取消引用" onClick={() => setQuotedMessage(null)}>×</button></div>}<div className="status-line"><i className={running ? 'pulse' : ''}/>{status}</div><div className="input-row"><textarea ref={composerRef} value={input} disabled={loadingSession} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }} placeholder="请输入您的问题，Enter 发送…" />{running ? <button className="stop" onClick={stop}>停止</button> : <button className="send" disabled={loadingSession} onClick={() => void send()}>发送</button>}</div></div>
        </div>
        {toast && <div className="chat-toast" role="status" aria-live="polite">✓ {toast}</div>}
        {showContact && <ContactForm sessionId={sessionId} summary={contactSummary} onClose={() => setShowContact(false)} />}
      </section> : <AdminConsole />}
    </main>
  </div>
}

function KnowledgeImages({ images }: { images: KnowledgeImage[] }) {
  return <div className="knowledge-images">{images.map(image => <figure key={image.id}><img src={image.url} alt={image.alt_text} width={image.width} height={image.height} loading="lazy" decoding="async" referrerPolicy="no-referrer"/><figcaption>{image.location}</figcaption></figure>)}</div>
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
  const [adminSessions, setAdminSessions] = useState<AdminSession[]>([])
  const [adminSessionScope, setAdminSessionScope] = useState<'all' | 'active' | 'archived' | 'deleted'>('all')
  const [notice, setNotice] = useState('')
  const [knowledgePath, setKnowledgePath] = useState('')
  const [importing, setImporting] = useState(false)
  const [importDetails, setImportDetails] = useState<ImportDetail[]>([])
  const load = async () => {
    const config = await api<{ active_provider: string; providers: Provider[] }>('/api/admin/providers'); setProviders(config.providers); setActive(config.active_provider); setModels(Object.fromEntries(config.providers.map(p => [p.provider, p.model])))
    const location = await api<KnowledgeLocation>('/api/admin/knowledge/location'); setKnowledgePath(current => current || location.path)
    setDocuments(await api('/api/admin/documents')); setContacts(await api('/api/admin/contacts')); setAdminSessions(await api(adminSessionListPath(adminSessionScope)))
  }
  useEffect(() => { void load() }, [])
  useEffect(() => { void load() }, [adminSessionScope])
  const save = async (provider: string) => { await api(`/api/admin/providers/${provider}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: models[provider], api_key: keys[provider] || null }) }); setKeys({ ...keys, [provider]: '' }); setNotice(`${provider} 配置已安全保存`); await load() }
  const test = async (provider: string) => { const result = await api<{ message: string }>(`/api/admin/providers/${provider}/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: models[provider], api_key: keys[provider] || null }) }); setNotice(result.message); await load() }
  const activate = async (provider: string) => { await api('/api/admin/active-provider', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider }) }); setNotice('已切换，下一条消息生效'); await load() }
  const importKnowledge = async () => {
    if (!knowledgePath.trim() || importing) return
    setImporting(true)
    try {
      const result = await api<KnowledgeImportResult>('/api/admin/knowledge/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: knowledgePath, recursive: true }) })
      setNotice(`已导入 ${result.imported_count} 个文档，跳过 ${result.skipped_count} 个，失败 ${result.failed_count} 个`)
      setImportDetails([...result.failed.map(item => ({ ...item, kind: '失败' as const })), ...result.skipped.map(item => ({ ...item, kind: '跳过' as const }))])
      await load()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '知识库导入失败')
      setImportDetails([])
    } finally { setImporting(false) }
  }
  const uploadSelected = async (selected: File[]) => {
    if (!selected.length || importing) return
    const supported = ['.pdf', '.docx', '.md', '.txt']
    const files = selected.filter(file => supported.some(extension => file.name.toLowerCase().endsWith(extension)))
    const skipped: ImportIssue[] = selected.filter(file => !files.includes(file)).map(file => ({ path: file.name, display_name: file.name, reason: '不支持的文件格式' }))
    for (const file of files.slice(500)) skipped.push({ path: file.name, display_name: file.name, reason: '单次最多导入 500 个文档' })
    const failed: ImportIssue[] = []
    let importedCount = 0
    setImporting(true)
    try {
      for (const file of files.slice(0, 500)) {
        const body = new FormData(); body.append('file', file)
        try {
          await api('/api/admin/documents', { method: 'POST', body })
          importedCount += 1
        } catch (error) {
          const issue = { path: file.name, display_name: file.name, reason: error instanceof Error ? error.message : '文档解析失败' }
          if (error instanceof ApiError && error.status === 409) skipped.push(issue)
          else failed.push(issue)
        }
      }
      setNotice(`已导入 ${importedCount} 个文档，跳过 ${skipped.length} 个，失败 ${failed.length} 个`)
      setImportDetails([...failed.map(item => ({ ...item, kind: '失败' as const })), ...skipped.map(item => ({ ...item, kind: '跳过' as const }))])
      await load()
    } finally { setImporting(false) }
  }
  const removeDoc = async (id: string) => { await api(`/api/admin/documents/${id}`, { method: 'DELETE' }); await load() }
  const restoreAdminSession = async (id: string) => { await api(`/api/chat/sessions/${id}/restore`, { method: 'POST' }); setNotice('会话已恢复到当前会话列表'); await load() }
  return <div className="admin-grid">
    {notice && <div className="notice">{notice}</div>}
    <section className="card wide"><div className="card-title"><div><span className="eyebrow">SESSION OPERATIONS</span><h2>会话管理</h2></div><div className="admin-session-filters">{(['all', 'active', 'archived', 'deleted'] as const).map(scope => <button className={adminSessionScope === scope ? 'active' : ''} key={scope} onClick={() => setAdminSessionScope(scope)}>{{ all: '全部', active: '当前', archived: '已归档', deleted: '已删除' }[scope]}</button>)}</div></div><p className="muted">安全删除仅隐藏会话，不清除消息、运行和联系记录；可在此恢复。</p><div className="admin-session-list">{adminSessions.length ? adminSessions.map(item => <div className="admin-session-row" key={item.id}><div className="admin-session-main"><strong>{item.title}</strong><small>{item.deleted_at ? '已删除' : item.archived_at ? '已归档' : item.status === 'running' ? '回答中' : '当前'} · 更新于 {new Date(item.updated_at).toLocaleString('zh-CN')}</small></div><div className="admin-session-stats"><span>消息 {item.message_count}</span><span>运行 {item.run_count}</span><span className={item.error_count ? 'has-error' : ''}>错误 {item.error_count}</span><span>联系 {item.contact_count}</span><span>{item.provider ? `${item.provider} / ${item.model}` : '暂无模型调用'}</span>{item.last_error_code && <span className="has-error">最近错误：{item.last_error_code}</span>}</div>{(item.archived_at || item.deleted_at) && <button onClick={() => void restoreAdminSession(item.id)}>恢复</button>}</div>) : <div className="empty">当前筛选下没有会话</div>}</div></section>
    <section className="card wide"><div className="card-title"><div><span className="eyebrow">MODEL GATEWAY</span><h2>在线模型</h2></div><span className="badge">当前：{active}</span></div><p className="muted">密钥在浏览器中只写，后端使用本地主密钥加密保存，不会再次回显。</p><div className="provider-grid">{providers.map(p => <div className={`provider-card ${active === p.provider ? 'selected' : ''}`} key={p.provider}><div className="provider-head"><div><strong>{p.provider === 'deepseek' ? 'DeepSeek' : 'OpenAI'}</strong><small>{p.configured ? '密钥已配置' : '尚未配置'}</small></div><i className={p.configured ? 'ok' : ''}/></div><label>模型名<input value={models[p.provider] || ''} onChange={e => setModels({ ...models, [p.provider]: e.target.value })}/></label><label>API Key<input type="password" autoComplete="new-password" value={keys[p.provider] || ''} onChange={e => setKeys({ ...keys, [p.provider]: e.target.value })} placeholder={p.configured ? '留空表示不修改' : '在此录入，仅发送给本机后端'}/></label><div className="actions"><button onClick={() => void test(p.provider)}>测试连接</button><button onClick={() => void save(p.provider)}>保存</button><button className="primary" disabled={!p.configured || active === p.provider} onClick={() => void activate(p.provider)}>设为当前</button></div>{p.last_test_message && <small className="test-result">上次测试：{p.last_test_message}</small>}</div>)}</div></section>
    <section className="card"><div className="card-title"><div><span className="eyebrow">LOCAL KNOWLEDGE</span><h2>知识文档</h2></div><div className="picker-actions"><label className="upload">选择文件<input type="file" multiple accept=".pdf,.doc,.docx,.wps,.rtf,.odt,.xls,.xlsx,.et,.ods,.csv,.tsv,.ppt,.pptx,.dps,.odp,.md,.txt,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp" onChange={e => { const files = Array.from(e.currentTarget.files || []); e.currentTarget.value = ''; void uploadSelected(files) }}/></label><label className="upload">选择目录<input ref={element => { element?.setAttribute('webkitdirectory', '') }} type="file" multiple accept=".pdf,.doc,.docx,.wps,.rtf,.odt,.xls,.xlsx,.et,.ods,.csv,.tsv,.ppt,.pptx,.dps,.odp,.md,.txt,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp" onChange={e => { const files = Array.from(e.currentTarget.files || []); e.currentTarget.value = ''; void uploadSelected(files) }}/></label></div></div><p className="muted">支持 PDF、Office/WPS、OpenDocument、表格、演示文稿、文本、网页和图片；扫描件及嵌入图片自动 OCR。</p><div className="path-import"><input value={knowledgePath} onChange={e => setKnowledgePath(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void importKnowledge() }} placeholder="输入文件或目录的完整地址"/></div><div className="list">{documents.length ? documents.map(d => <div className="list-row" key={d.id}><div><strong>{d.display_name}</strong><small>{(d.size / 1024).toFixed(1)} KB · {d.chunk_count} 个片段</small></div><button className="danger" onClick={() => void removeDoc(d.id)}>删除</button></div>) : <div className="empty">还没有知识文档</div>}</div>{importDetails.length ? <div className="import-report"><strong>逐文件处理结果</strong>{importDetails.map((item, index) => <div className={`import-issue ${item.kind === '失败' ? 'failed' : 'skipped'}`} key={`${item.path}-${index}`}><span>{item.kind}</span><div><b>{item.display_name || item.path}</b><small>{item.reason}</small></div></div>)}</div> : null}<div className="knowledge-actions"><button className="primary" disabled={importing} onClick={() => void importKnowledge()}>{importing ? '正在导入…' : '扫描并导入'}</button></div></section>
    <section className="card"><div className="card-title"><div><span className="eyebrow">CONTACT LOG</span><h2>联系登记</h2></div><span className="badge">{contacts.length}</span></div><div className="list">{contacts.length ? contacts.map(c => <div className="list-row contact" key={c.id}><div><strong>{c.name}</strong><small>{c.phone || c.email}</small><p>{c.summary}</p></div></div>) : <div className="empty">暂无登记记录</div>}</div></section>
  </div>
}
