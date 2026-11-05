import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DocumentRenderer } from '../components/DocumentRenderer'
import { setToken } from '../lib'
import { deferred, json, mockFetch, settle } from './helpers'

vi.mock('react-pdf', async () => {
  const React = await import('react')
  return {
    pdfjs: { GlobalWorkerOptions: {} },
    Document: ({ children, file, suspense, onLoadSuccess, onLoadError }: {
      children: React.ReactNode; file: string; suspense?: boolean
      onLoadSuccess?: (data: { numPages: number }) => void; onLoadError?: (error: Error) => void
    }) => {
      React.useEffect(() => { file === 'blob:broken' ? onLoadError?.(new Error('worker failed')) : onLoadSuccess?.({ numPages: 3 }) }, [])
      return <div data-testid="pdf-document" data-suspense={String(suspense)}>{children}</div>
    },
    Page: ({ pageNumber, suspense }: { pageNumber: number; suspense?: boolean }) =>
      <div data-testid="pdf-page-number" data-suspense={String(suspense)}>{pageNumber}</div>,
  }
})

describe('document readers', () => {
  it('sanitizes converted HTML and downloads the original with same-origin bearer auth only', async () => {
    setToken('A')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const create = vi.fn(() => 'blob:original'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    const { calls } = mockFetch(({ path }) => path === '/lesson-raw/14' ? new Response('document') : undefined)
    const { container } = render(<DocumentRenderer contentType="html" convertedHtml={'<h2>安全内容</h2><script>alert(1)</script><p style="color:red">Text</p><a href="javascript:alert(1)">bad</a>'}
      rawFileUrl={`${window.location.origin}/api/lesson-raw/14?token=leak`} />)
    expect(screen.getByRole('heading', { name: '安全内容' })).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByText('Text')).not.toHaveAttribute('style')
    expect(screen.getByText('bad')).not.toHaveAttribute('href', 'javascript:alert(1)')
    // jsdom's test origin varies: canonical relative URL is always permitted.
    fireEvent.click(screen.queryByRole('button', { name: '下载原始文件' })!)
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(calls[0].url).toBe('/api/lesson-raw/14')
    expect(new Headers(calls[0].options.headers).get('Authorization')).toBe('Bearer A')
    expect(revoke).toHaveBeenCalledWith('blob:original')
  })

  it('never sends a token or automatically fetches an external document URL', () => {
    setToken('private-token')
    const { calls } = mockFetch(() => undefined)
    render(<DocumentRenderer contentType="pdf" content="## 文档摘要" rawFileUrl="https://external.example/api/lesson-raw/16" />)
    expect(screen.getByRole('heading', { name: '文档摘要' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '下载 PDF' })).not.toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('loads PPT coordinates as React nodes, switches pages, and falls back to sanitized text if preview fails', async () => {
    setToken('A')
    const { calls } = mockFetch(({ path }) => path === '/lesson-preview/7' ? json({ version: 1, width: 960, height: 540, slides: [
      { shapes: [{ type: 'text', x: 12, y: 30, width: 400, height: 80, paragraphs: [{ align: 'left', runs: [{ text: '第一张', fontSize: 24, color: '#222222' }] }] }] },
      { shapes: [{ type: 'text', x: 24, y: 40, width: 300, height: 60, paragraphs: [{ runs: [{ text: '第二张' }] }] }] },
    ] }) : path === '/lesson-preview/8' ? json({ message: 'unavailable' }, 500) : undefined)
    const { rerender, container } = render(<DocumentRenderer contentType="ppt" rawFileUrl="/api/lesson-raw/7" convertedHtml="<h3>保底文字</h3>" />)
    expect(await screen.findByText('第一张')).toBeInTheDocument()
    expect(container.querySelector('.ppt-stage .ppt-shape')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '阅读本页文字' }))
    expect(screen.getByRole('region', { name: '第 1 张幻灯片文字' })).toHaveTextContent('第一张')
    fireEvent.click(screen.getByRole('button', { name: '下一张' }))
    expect(screen.getByRole('region', { name: '第 2 张幻灯片文字' })).toHaveTextContent('第二张')
    expect(calls[0].url).toBe('/api/lesson-preview/7')
    expect(new Headers(calls[0].options.headers).get('Authorization')).toBe('Bearer A')
    rerender(<DocumentRenderer contentType="ppt" rawFileUrl="/api/lesson-raw/8" convertedHtml="<h3>保底文字</h3><style>body{display:none}</style>" />)
    expect(await screen.findByRole('heading', { name: '保底文字' })).toBeInTheDocument()
    expect(container.querySelector('style')).toBeNull()
    expect(screen.queryByText('第二张')).not.toBeInTheDocument()
  })

  it('removes a previously loaded PPT slide immediately on session switch', async () => {
    setToken('A')
    let requests = 0
    mockFetch(({ path }) => path === '/lesson-preview/21' ? (++requests === 1 ? json({
      version: 1, width: 960, height: 540, slides: [{ shapes: [{ type: 'text', x: 0, y: 0, width: 500, height: 80,
        paragraphs: [{ runs: [{ text: '旧会话机密幻灯片' }] }] }] }],
    }) : new Promise<Response>(() => {})) : undefined)
    render(<DocumentRenderer contentType="ppt" rawFileUrl="/api/lesson-raw/21" />)
    expect(await screen.findByText('旧会话机密幻灯片')).toBeInTheDocument()
    await settle(() => setToken('B'))
    expect(screen.queryByText('旧会话机密幻灯片')).not.toBeInTheDocument()
  })

  it('discards a PDF response from the previous session even when fetch ignores abort', async () => {
    setToken('A')
    const pending = deferred<Response>(), create = vi.fn(() => 'blob:pdf')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }))
    let requests = 0
    const { calls } = mockFetch(({ path }) => path === '/lesson-raw/20' ? (++requests === 1 ? pending.promise : new Promise<Response>(() => {})) : undefined)
    const { unmount } = render(<DocumentRenderer contentType="pdf" rawFileUrl="/api/lesson-raw/20" convertedHtml="<p>PDF 文字副本</p>" />)
    await waitFor(() => expect(calls.some(call => call.path === '/lesson-raw/20')).toBe(true))
    setToken('B')
    await settle(() => pending.resolve(new Response('old private PDF')))
    expect(create).not.toHaveBeenCalled()
    expect(screen.queryByTestId('pdf-document')).not.toBeInTheDocument()
    unmount()
  })

  it('expands readable PDF text by default on narrow screens', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:mobile'), revokeObjectURL: vi.fn() }))
    mockFetch(({ path }) => path === '/lesson-raw/16' ? new Response('PDF') : undefined)
    render(<DocumentRenderer contentType="pdf" rawFileUrl="/api/lesson-raw/16" convertedHtml="<p>手机版可读的文档文字</p>" />)
    expect(await screen.findByRole('region', { name: 'PDF 提取文字' })).toHaveTextContent('手机版可读的文档文字')
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled())
    expect(screen.getByRole('button', { name: '收起提取文字' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('shows extracted text and guarded download if the PDF worker fails', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:broken'), revokeObjectURL: vi.fn() }))
    mockFetch(({ path }) => path === '/lesson-raw/17' ? new Response('broken') : undefined)
    render(<DocumentRenderer contentType="pdf" rawFileUrl="/api/lesson-raw/17" convertedHtml="<p>可读的文字副本</p>" />)
    expect(await screen.findByText('可读的文字副本')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下载 PDF' })).toBeEnabled()
    expect(screen.queryByTestId('pdf-document')).not.toBeInTheDocument()
  })

  it('revokes a loaded PDF blob and shows extracted text when the PDF cannot be fetched', async () => {
    const create = vi.fn(() => 'blob:preview'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    mockFetch(({ path }) => path === '/lesson-raw/19' ? new Response('PDF') : undefined)
    const reader = render(<DocumentRenderer contentType="pdf" rawFileUrl="/api/lesson-raw/19" convertedHtml="<p>PDF 文字副本</p>" />)
    expect(await screen.findByTestId('pdf-document')).toHaveAttribute('data-suspense', 'false')
    expect(screen.getByTestId('pdf-page-number')).toHaveAttribute('data-suspense', 'false')
    expect(screen.queryByRole('region', { name: 'PDF 提取文字' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '阅读提取文字' }))
    expect(screen.getByRole('region', { name: 'PDF 提取文字' })).toHaveTextContent('PDF 文字副本')
    fireEvent.click(screen.getByRole('button', { name: '收起提取文字' }))
    expect(screen.queryByRole('region', { name: 'PDF 提取文字' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByTestId('pdf-page-number')).toHaveTextContent('2')
    reader.unmount()
    expect(revoke).toHaveBeenCalledWith('blob:preview')

    mockFetch(({ path }) => path === '/lesson-raw/18' ? new Response('Forbidden', { status: 403 }) : undefined)
    render(<DocumentRenderer contentType="pdf" rawFileUrl="/api/lesson-raw/18" convertedHtml="<p>PDF 文字副本</p>" />)
    expect(await screen.findByText('PDF 文字副本')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下载 PDF' })).toBeEnabled()
  })
})
