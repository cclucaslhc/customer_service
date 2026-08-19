export function sessionIdFromSearch(search: string): string {
  return new URLSearchParams(search).get('session')?.trim() || ''
}

export function sessionUrl(href: string, sessionId: string): string {
  const url = new URL(href)
  if (sessionId) url.searchParams.set('session', sessionId)
  else url.searchParams.delete('session')
  return `${url.pathname}${url.search}${url.hash}`
}

export function sessionListPath(scope: 'active' | 'archived'): string {
  return `/api/chat/sessions?scope=${scope}`
}

export function adminSessionListPath(scope: 'all' | 'active' | 'archived' | 'deleted'): string {
  return `/api/admin/chat/sessions?scope=${scope}`
}

export function adminSessionActions(session: { archived_at?: string; deleted_at?: string }) {
  if (session.deleted_at) return { canArchive: false, canRestore: true, canDelete: false, lifecycle: 'deleted' as const }
  if (session.archived_at) return { canArchive: false, canRestore: true, canDelete: true, lifecycle: 'archived' as const }
  return { canArchive: true, canRestore: false, canDelete: true, lifecycle: 'active' as const }
}

export function quoteMessageContent(role: 'user' | 'assistant', content: string, limit = 500): string {
  const clean = content.trim()
  const excerpt = clean.length > limit ? `${clean.slice(0, limit).trimEnd()}…` : clean
  const quoted = excerpt.split('\n').map(line => `> ${line}`).join('\n')
  return `> 引用${role === 'user' ? '我的消息' : 'AI 客服回答'}\n${quoted}\n\n`
}

export function messageFeedbackPath(sessionId: string, messageId: string): string {
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`
}

export type AdminModule = 'overview' | 'sessions' | 'prompts' | 'knowledge' | 'models' | 'contacts' | 'audit'

export function adminRouteFromPath(pathname: string): { module: AdminModule; entityId: string } {
  const parts = pathname.split('/').filter(Boolean)
  const allowed: AdminModule[] = ['overview', 'sessions', 'prompts', 'knowledge', 'models', 'contacts', 'audit']
  const module = allowed.includes(parts[1] as AdminModule) ? parts[1] as AdminModule : 'overview'
  return { module, entityId: parts[2] || '' }
}

export function adminPath(module: AdminModule, entityId = ''): string {
  return `/admin/${module}${entityId ? `/${encodeURIComponent(entityId)}` : ''}`
}
