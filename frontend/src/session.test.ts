import { describe, expect, it } from 'vitest'
import { adminPath, adminRouteFromPath, adminSessionActions, adminSessionListPath, messageFeedbackPath, quoteMessageContent, sessionIdFromSearch, sessionListPath, sessionUrl } from './session'

describe('session URL helpers', () => {
  it('restores the selected session from query parameters', () => {
    expect(sessionIdFromSearch('?session=session-123&source=history')).toBe('session-123')
    expect(sessionIdFromSearch('?source=history')).toBe('')
  })

  it('sets and clears the session without losing other URL state', () => {
    expect(sessionUrl('http://127.0.0.1:8000/?source=history#chat', 'session-456')).toBe('/?source=history&session=session-456#chat')
    expect(sessionUrl('http://127.0.0.1:8000/?source=history&session=old#chat', '')).toBe('/?source=history#chat')
  })

  it('builds lifecycle-filtered user and admin list paths', () => {
    expect(sessionListPath('active')).toBe('/api/chat/sessions?scope=active')
    expect(sessionListPath('archived')).toBe('/api/chat/sessions?scope=archived')
    expect(adminSessionListPath('deleted')).toBe('/api/admin/chat/sessions?scope=deleted')
    expect(adminSessionListPath('all')).toBe('/api/admin/chat/sessions?scope=all')
  })

  it('restores all admin modules and session detail deep links', () => {
    expect(adminRouteFromPath('/admin/prompts')).toEqual({ module: 'prompts', entityId: '' })
    expect(adminRouteFromPath('/admin/sessions/session-123')).toEqual({ module: 'sessions', entityId: 'session-123' })
    expect(adminRouteFromPath('/admin/unknown')).toEqual({ module: 'overview', entityId: '' })
    expect(adminPath('sessions', 'session-123')).toBe('/admin/sessions/session-123')
  })

  it('does not offer delete again after a session is safely deleted', () => {
    expect(adminSessionActions({})).toEqual({ canArchive: true, canRestore: false, canDelete: true, lifecycle: 'active' })
    expect(adminSessionActions({ archived_at: '2026-08-19T12:00:00Z' })).toEqual({ canArchive: false, canRestore: true, canDelete: true, lifecycle: 'archived' })
    expect(adminSessionActions({ deleted_at: '2026-08-19T12:00:00Z' })).toEqual({ canArchive: false, canRestore: true, canDelete: false, lifecycle: 'deleted' })
  })

  it('builds editable message quotes and feedback endpoints', () => {
    expect(quoteMessageContent('assistant', '第一行\n第二行')).toBe('> 引用AI 客服回答\n> 第一行\n> 第二行\n\n')
    expect(quoteMessageContent('user', '123456', 4)).toBe('> 引用我的消息\n> 1234…\n\n')
    expect(messageFeedbackPath('session 1', 'message/2')).toBe('/api/chat/sessions/session%201/messages/message%2F2/feedback')
  })
})
