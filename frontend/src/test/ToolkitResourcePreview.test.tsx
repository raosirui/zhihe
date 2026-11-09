import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ToolkitDetailPage } from '../pages/ToolkitDetailPage'
import { setToken } from '../lib'
import { deferred, json, mockFetch, renderPage, settle, toolkit } from './helpers'

vi.mock('react-pdf', async () => {
  const React = await import('react')
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: ({ children, onLoadSuccess }: { children: React.ReactNode; onLoadSuccess?: (data: { numPages: number }) => void }) => {
      React.useEffect(() => { onLoadSuccess?.({ numPages: 2 }) }, [])
      return <div data-testid="resource-pdf-document">{children}</div>
    },
    Page: ({ pageNumber }: { pageNumber: number }) => <div data-testid="resource-pdf-page">{pageNumber}</div>,
  }
})

const item = (name = 'manual.docx', locked = false) => ({ ...toolkit(8), resourceName: name,
  resources: [{ refId: 12, name, size: 42, locked, downloadUrl: '/api/toolkits/8/resources/12/download' }] })

describe('authorized toolkit resource previews', () => {
  it('requests the exact authorized resource route and sanitizes converted DOC/PPT HTML', async () => {
    setToken('buyer-session')
    const { calls } = mockFetch(({ path }) => path === '/toolkits/8' ? json(item()) : path.endsWith('/resources/12/preview') ?
      new Response('<h2>研究手册</h2><script>alert(1)</script><a href="javascript:alert(1)" style="color:red">恶意链接</a>',
        { headers: { 'Content-Type': 'text/html; charset=utf-8' } }) : undefined)
    const result = renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/8')
    expect(await screen.findByText('manual.docx')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByRole('heading', { name: '研究手册' })).toBeInTheDocument()
    const dialog = screen.getByRole('dialog', { name: '预览资源：manual.docx' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.querySelector('script')).toBeNull()
    expect(screen.getByText('恶意链接')).not.toHaveAttribute('style')
    expect(screen.getByText('恶意链接')).not.toHaveAttribute('href', 'javascript:alert(1)')
    const preview = calls.find(call => call.path.endsWith('/resources/12/preview'))!
    expect(preview.url).toBe('/api/toolkits/8/resources/12/preview')
    expect(new Headers(preview.options.headers).get('Authorization')).toBe('Bearer buyer-session')
    fireEvent.click(screen.getByRole('button', { name: '关闭预览' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    result.unmount()
  })

  it('previews PDF via a local worker and revokes the blob URL when closed', async () => {
    setToken('buyer-session')
    const create = vi.fn(() => 'blob:resource-pdf'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    mockFetch(({ path }) => path === '/toolkits/8' ? json(item('guide.pdf')) : path.endsWith('/resources/12/preview') ?
      new Response('PDF data', { headers: { 'Content-Type': 'application/pdf' } }) : undefined)
    renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/8')
    await screen.findByText('guide.pdf')
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByTestId('resource-pdf-document')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByTestId('resource-pdf-page')).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: '关闭预览' }))
    expect(revoke).toHaveBeenCalledWith('blob:resource-pdf')
  })

  it('never shows preview for locked resources and reports unsupported previews without downloading', async () => {
    setToken('buyer-session')
    const { calls } = mockFetch(({ path }) => path === '/toolkits/8' ? json(item('archive.zip', false)) : path.endsWith('/preview') ?
      json({ message: '此文件不支持预览' }, 415) : undefined)
    const result = renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/8')
    await screen.findByText('archive.zip')
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('此文件不支持预览')
    expect(calls.some(call => call.path.endsWith('/download'))).toBe(false)
    await result.navigate('/toolkits/8')
    result.unmount()

    mockFetch(({ path }) => path === '/toolkits/8' ? json(item('locked.pdf', true)) : undefined)
    renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/8')
    await screen.findByText('locked.pdf')
    expect(screen.queryByRole('button', { name: '预览' })).not.toBeInTheDocument()
  })

  it('discards stale private responses and closes the modal on logout', async () => {
    setToken('buyer-session')
    const pending = deferred<Response>(), create = vi.fn(() => 'blob:stale')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }))
    const { calls } = mockFetch(({ path }) => path === '/toolkits/8' ? json(item('private.png')) :
      path === '/toolkits/8/resources/12/preview' ? pending.promise : path === '/auth/logout' ? json({ ok: true }) : undefined)
    renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/8', undefined, true)
    await screen.findByText('private.png')
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    const sent = calls.find(call => call.path.endsWith('/preview'))!
    fireEvent.click(screen.getByRole('button', { name: 'Logout session' }))
    await waitFor(() => expect(sent.options.signal?.aborted).toBe(true))
    await settle(() => pending.resolve(new Response('secret', { headers: { 'Content-Type': 'image/png' } })))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })
})
