import { afterEach, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import App from './App'
import AdminConsole from './AdminConsole'

afterEach(() => vi.unstubAllGlobals())
it('customers never render management UI even at an admin URL', () => {
  vi.stubGlobal('window', {location:{pathname:'/admin/models',search:'',href:'http://localhost/admin/models'}})
  const html = renderToStaticMarkup(<App role="customer" />)
  expect(html).toContain('在线客服')
  expect(html).not.toContain('企业管理中心')
  expect(html).not.toContain('系统管理')
})
it('support navigation hides configuration, knowledge and audit modules', () => {
  vi.stubGlobal('window', {location:{pathname:'/admin/models',search:'',href:'http://localhost/admin/models'}})
  const html = renderToStaticMarkup(<AdminConsole role="support" />)
  expect(html).toContain('会话管理')
  expect(html).toContain('已启用登录')
  expect(html).not.toContain('模型配置')
  expect(html).not.toContain('审计日志')
})
it('admin model module exposes explicit price and budget controls without key disclosure', () => {
  vi.stubGlobal('window', {location:{pathname:'/admin/models',search:'',href:'http://localhost/admin/models'},
    history:{pushState:vi.fn()},addEventListener:vi.fn(),removeEventListener:vi.fn()})
  vi.stubGlobal('fetch', vi.fn())
  const html = renderToStaticMarkup(<AdminConsole role="admin" />)
  expect(html).toContain('价格与费用预算')
  expect(html).toContain('发布价格版本')
  expect(html).toContain('发布预算版本')
  expect(html).toContain('依据 SHA-256')
  expect(html).not.toContain('synthetic-a-key-only')
})
