import { afterEach, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import StorageSettings from './StorageSettings'
import AdminConsole from './AdminConsole'

afterEach(() => vi.unstubAllGlobals())

it('storage settings initially render no credentials and make no render-time request', () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const html = renderToStaticMarkup(<StorageSettings />)
  expect(html).toContain('私有对象存储')
  expect(html).toContain('正在读取配置')
  expect(html).not.toContain('type="password"')
  expect(fetch).not.toHaveBeenCalled()
})

it('support cannot render storage configuration even at the knowledge URL', () => {
  vi.stubGlobal('window', {location:{pathname:'/admin/knowledge',search:'',href:'http://localhost/admin/knowledge'}})
  const html = renderToStaticMarkup(<AdminConsole role="support" />)
  expect(html).toContain('会话管理')
  expect(html).not.toContain('私有对象存储')
  expect(html).not.toContain('Access Key')
})
