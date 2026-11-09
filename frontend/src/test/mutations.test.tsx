import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { CheckoutPage } from '../pages/CheckoutPage'
import { ToolkitDetailPage } from '../pages/ToolkitDetailPage'
import { ToolkitListPage } from '../pages/ToolkitListPage'
import { LearnPage } from '../pages/LearnPage'
import { BatchPublishPage } from '../pages/BatchPublishPage'
import { AuthModal } from '../components/AuthModal'
import { setToken } from '../lib'
import { deferred, json, lesson, mockFetch, order, other, renderPage, settle, toolkit } from './helpers'

describe('mutation/request scope regressions', () => {
  it('checkout clears old order/cache and pending old results on a user switch', async () => {
    setToken('A'); const pending = deferred<Response>(); let switched = false
    mockFetch(({ path }) => {
      if (path === '/auth/login') { switched = true; return json({ token: 'B', user: other }) }
      if (path === '/me/orders') return switched ? json([]) : pending.promise
    })
    renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1', { order: order(1) }, true)
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    await screen.findByText('Other'); await screen.findByText('未找到该订单，可能不属于当前账号。')
    await settle(() => pending.resolve(json([order(1)])))
    expect(screen.queryByText('ORDER-1')).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: /确认支付/ })).not.toBeInTheDocument()
  })
  it('buy and favorite completions cannot navigate or overwrite a newer detail route', async () => {
    setToken('A'); const buy = deferred<Response>(), favorite = deferred<Response>()
    mockFetch(({ path }) => {
      if (path.startsWith('/toolkits/') && !path.endsWith('/favorite')) return json({ ...toolkit(Number(path.split('/').pop())), price: 10, canLearn: false })
      if (path.endsWith('/favorite')) return favorite.promise
      if (path === '/orders') return buy.promise
    })
    const result = renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/1')
    await screen.findByText('Toolkit 1'); await waitFor(() => expect(screen.getByRole('button', { name: '立即购买' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '收藏工具包' })); fireEvent.click(screen.getByRole('button', { name: '立即购买' }))
    await result.navigate('/toolkits/2'); await screen.findByText('Toolkit 2')
    await settle(() => { buy.resolve(json(order(1))); favorite.resolve(json({ favorited: true })) })
    expect(result.router.state.location.pathname).toBe('/toolkits/2')
    expect(screen.getByRole('button', { name: '收藏工具包' })).toBeEnabled(); expect(toast.success).not.toHaveBeenCalled()
  })
  it('stale list catch/finally cannot expose an error/empty state in a newer request', async () => {
    const old = deferred<Response>(), next = deferred<Response>()
    mockFetch(({ path }) => path.startsWith('/toolkits?') ? path.includes('q=new') ? next.promise : old.promise : undefined)
    const result = renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?q=old')
    await result.navigate('/toolkits?q=new'); await settle(() => old.reject(new Error('old error')))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(screen.queryByText('没有找到匹配的工具包')).not.toBeInTheDocument()
    await settle(() => next.resolve(json({ items: [toolkit(2)], total: 1, page: 1 })))
    expect(screen.getByText('Toolkit 2')).toBeInTheDocument()
  })
  it('failed next stays on the current lesson, and explicit completion does not advance', async () => {
    setToken('A'); let fail = true
    mockFetch(({ path }) => path === '/toolkits/1' ? json(toolkit(1, [lesson(11), lesson(12)])) : path === '/lessons/11/complete' ? fail ? Promise.reject(new Error('offline')) : json({ ok: true }) : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    await screen.findByRole('heading', { name: 'Lesson 11' }); fireEvent.click(screen.getByRole('button', { name: '下一节' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '本节完成' })).toBeEnabled())
    expect(screen.getByRole('heading', { name: 'Lesson 11' })).toBeInTheDocument()
    fail = false; fireEvent.click(screen.getByRole('button', { name: '本节完成' }))
    await screen.findByText('已完成'); expect(screen.getByRole('heading', { name: 'Lesson 11' })).toBeInTheDocument()
  })
  it('short password remains valid for seven-character legacy credentials', async () => {
    mockFetch(({ path }) => path === '/auth/login' ? json({ token: 'B', user: other }) : undefined)
    renderPage(<AuthModal open onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText(/账号/), { target: { value: 'legacy' } }); fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Old1234' } })
    expect((screen.getByLabelText('密码') as HTMLInputElement).checkValidity()).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '登录' })); await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })
  it('disabled payment capability blocks checkout and purchase without claiming a real provider', async () => {
    setToken('A')
    const { calls } = mockFetch(({ path }) => path === '/wallet' ? json({ demoEnabled: false, canPay: false }) : path === '/me/orders' ? json([order(1)]) : path === '/toolkits/1' ? json({ ...toolkit(1), price: 10, canLearn: false }) : undefined)
    const result = renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1')
    await screen.findByText('ORDER-1'); expect(screen.getByRole('button', { name: /确认支付/ })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('尚未接入真实支付')
    expect(calls.some(x => x.path.endsWith('/pay'))).toBe(false)
    result.unmount()
    renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/1')
    await screen.findByText('Toolkit 1'); expect(screen.getByRole('button', { name: '立即购买' })).toBeDisabled()
    expect(calls.some(x => x.path === '/orders')).toBe(false)
  })
  it('batch polls the async job and renders per-item results from the report', async () => {
    setToken('A')
    const job = { jobId: 'a'.repeat(32), status: 'done', mode: 'skip', publish: true, total: 3, processed: 3, created: 1, updated: 1, skipped: 1, failed: 1,
      results: [{ index: 0, title: 'Created title', status: 'created', toolkitId: 1 }, { index: 1, title: 'Updated title', status: 'updated', toolkitId: 2 },
                { index: 2, title: 'Skipped title', status: 'skipped' }, { index: 3, title: 'Failed title', status: 'failed', error: 'bad input' }] }
    vi.stubGlobal('confirm', vi.fn(() => true))
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'safe prompt' }) : path === '/batch/jobs' ? json([]) : path === '/batch/upload' ? json({ jobId: job.jobId, status: 'queued' }, 202) : path === `/batch/jobs/${job.jobId}` ? json(job) : undefined)
    const result = renderPage(<BatchPublishPage />)
    await screen.findByText('开始批量上传')
    const fileInput = result.container.querySelector('input[type=file]')!
    fireEvent.change(fileInput, { target: { files: [new File(['zip'], 'safe.zip', { type: 'application/zip' })] } })
    fireEvent.click(screen.getByRole('button', { name: '开始批量上传' }))
    expect(await screen.findByText('共 3 条 · 创建 1 · 更新 1 · 跳过 1 · 失败 1')).toBeInTheDocument()
    expect(screen.getByText('Skipped title')).toBeInTheDocument(); expect(screen.getByText('bad input')).toBeInTheDocument()
    expect(screen.getByText('已更新')).toBeInTheDocument()
    // Reusing the native picker after a completed job must not show the old report.
    fireEvent.change(fileInput, { target: { files: [new File(['bad'], 'wrong.txt')] } })
    expect(screen.getByRole('alert')).toHaveTextContent('仅支持 .zip 文件')
    expect(screen.queryByText('Created title')).not.toBeInTheDocument()
    fireEvent.change(fileInput, { target: { files: [new File(['zip'], 'safe.zip')] } })
    expect(screen.getByRole('button', { name: '开始批量上传' })).toBeEnabled()
  })
  it('validates picker and drop ZIPs before upload, clears stale selection, and resets the native picker', async () => {
    setToken('A')
    const { calls } = mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([]) : undefined)
    renderPage(<BatchPublishPage />)
    const picker = await screen.findByLabelText('选择批量上传 ZIP 文件') as HTMLInputElement
    const upload = screen.getByRole('button', { name: '开始批量上传' })
    const good = new File(['zip'], 'archive.ZIP')
    fireEvent.change(picker, { target: { files: [good] } })
    expect(upload).toBeEnabled(); expect(picker.value).toBe('')
    fireEvent.change(picker, { target: { files: [new File(['x'], 'notes.txt')] } })
    expect(screen.getByRole('alert')).toHaveTextContent('仅支持 .zip 文件'); expect(upload).toBeDisabled()
    const large = new File(['x'], 'large.zip')
    Object.defineProperty(large, 'size', { value: 100 * 1024 * 1024 - 64 * 1024 + 1 })
    fireEvent.drop(picker.closest('label')!, { dataTransfer: { files: [large] } })
    expect(screen.getByRole('alert')).toHaveTextContent('预留 64 KB'); expect(upload).toBeDisabled()
    const limit = new File(['x'], 'limit.zip')
    Object.defineProperty(limit, 'size', { value: 100 * 1024 * 1024 - 64 * 1024 })
    fireEvent.change(picker, { target: { files: [limit] } })
    expect(upload).toBeEnabled(); expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(calls.filter(call => call.path === '/batch/upload')).toHaveLength(0)
  })

  it('explains and confirms destructive or public actions but permits a skip-mode draft without confirmation', async () => {
    setToken('A')
    const confirm = vi.fn(() => false); vi.stubGlobal('confirm', confirm)
    const { calls } = mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([]) : path === '/batch/upload' ? json({ jobId: 'b'.repeat(32) }, 202) : path === `/batch/jobs/${'b'.repeat(32)}` ? json({ jobId: 'b'.repeat(32), status: 'done', mode: 'skip', publish: false, total: 0, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0, results: [] }) : undefined)
    renderPage(<BatchPublishPage />)
    const picker = await screen.findByLabelText('选择批量上传 ZIP 文件')
    expect(screen.getByRole('radio', { name: '跳过（只建新）' })).toBeChecked()
    expect(screen.getByRole('radio', { name: '直接上架' })).toBeChecked()
    expect(screen.getByText(/覆盖更新会修改旧包内容（原上架状态不变）/)).toBeInTheDocument()
    fireEvent.change(picker, { target: { files: [new File(['zip'], 'ok.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '开始批量上传' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('直接公开上架'))
    expect(calls.some(call => call.path === '/batch/upload')).toBe(false)
    fireEvent.click(screen.getByRole('radio', { name: '存为草稿' }))
    fireEvent.click(screen.getByRole('radio', { name: '覆盖更新' }))
    expect(screen.getByRole('radio', { name: '覆盖更新' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '开始批量上传' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('覆盖本人同名工具包'))
    expect(calls.some(call => call.path === '/batch/upload')).toBe(false)
    fireEvent.click(screen.getByRole('radio', { name: '跳过（只建新）' }))
    fireEvent.click(screen.getByRole('button', { name: '开始批量上传' }))
    await waitFor(() => expect(calls.some(call => call.path === '/batch/upload')).toBe(true))
    const form = calls.find(call => call.path === '/batch/upload')!.options.body as FormData
    expect(form.get('mode')).toBe('skip'); expect(form.get('publish')).toBe('0'); expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('recovers an owned job from the recent list and discards an older job response after switching', async () => {
    setToken('A')
    const first = 'a'.repeat(32), second = 'b'.repeat(32), pending = deferred<Response>()
    const summary = (jobId: string) => ({ jobId, status: 'running', mode: 'skip', publish: false, total: 2, processed: 1, created: 1, updated: 0, skipped: 0, failed: 0, createdAt: '2026-11-09T00:00:00' })
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([summary(first), summary(second)]) : path === `/batch/jobs/${first}` ? pending.promise : path === `/batch/jobs/${second}` ? json({ ...summary(second), status: 'done', results: [{ index: 0, title: 'Second job', status: 'created' }] }) : undefined)
    renderPage(<BatchPublishPage />)
    expect(await screen.findByText('最近的批量任务')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('button', { name: '查看任务' })).toHaveLength(2))
    fireEvent.click(screen.getAllByRole('button', { name: '查看任务' })[0])
    expect(screen.getByRole('status')).toHaveTextContent('正在读取任务')
    fireEvent.click(screen.getAllByRole('button', { name: '查看任务' })[1])
    expect(await screen.findByText('Second job')).toBeInTheDocument()
    await settle(() => pending.resolve(json({ ...summary(first), status: 'done', results: [{ index: 0, title: 'Old first job', status: 'created' }] })))
    expect(screen.queryByText('Old first job')).not.toBeInTheDocument()
    expect(screen.getByText('Second job')).toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('retries even when the first poll fails, keeping the job busy until a terminal result', async () => {
    setToken('A')
    const jobId = 'c'.repeat(32); let attempts = 0
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([{ jobId, status: 'running', mode: 'skip', publish: false, total: 1, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0 }]) : path === `/batch/jobs/${jobId}` ? ++attempts === 1 ? Promise.reject(new Error('offline')) : json({ jobId, status: 'done', mode: 'skip', publish: false, total: 1, processed: 1, created: 1, updated: 0, skipped: 0, failed: 0, results: [{ index: 0, title: 'Recovered job', status: 'created' }] }) : undefined)
    renderPage(<BatchPublishPage />)
    await screen.findByRole('button', { name: '查看任务' })
    vi.useFakeTimers()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看任务' })); await Promise.resolve() })
    expect(screen.getByRole('alert')).toHaveTextContent('将在 5 秒后自动重试')
    expect(screen.getByRole('button', { name: '正在查看进度…' })).toBeDisabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('Recovered job')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(attempts).toBe(2)
  })

  it('does not poll or show a previous result after navigating away during a job request', async () => {
    setToken('A')
    const jobId = 'e'.repeat(32), pending = deferred<Response>(); let polls = 0
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([{ jobId, status: 'running', mode: 'skip', publish: false, total: 1, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0 }]) : path === `/batch/jobs/${jobId}` ? (polls++, pending.promise) : undefined)
    const result = renderPage(<BatchPublishPage />)
    await screen.findByRole('button', { name: '查看任务' })
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }))
    await result.navigate('/another')
    await settle(() => pending.resolve(json({ jobId, status: 'running', mode: 'skip', publish: false, total: 1, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0, results: [] })))
    expect(screen.getByText('Another route')).toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
    expect(polls).toBe(1)
  })

  it('does not expose a prior owner job after logout or let a stale response change the page', async () => {
    setToken('A')
    const jobId = 'd'.repeat(32), pending = deferred<Response>()
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'prompt' }) : path === '/batch/jobs' ? json([{ jobId, status: 'running', mode: 'skip', publish: true, total: 1, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0 }]) : path === `/batch/jobs/${jobId}` ? pending.promise : undefined)
    const result = renderPage(<BatchPublishPage />, '/', '/', undefined, true)
    await screen.findByRole('button', { name: '查看任务' })
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }))
    fireEvent.click(screen.getByRole('button', { name: 'Logout session' }))
    await screen.findByText('登录后即可批量上传工具包')
    await settle(() => pending.resolve(json({ jobId, status: 'done', mode: 'skip', publish: true, total: 1, processed: 1, created: 1, updated: 0, skipped: 0, failed: 0, results: [{ index: 0, title: 'Private old owner item', status: 'created' }] })))
    expect(screen.queryByText('Private old owner item')).not.toBeInTheDocument()
    expect(screen.queryByText('最近的批量任务')).not.toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
    await result.navigate('/another')
    expect(screen.getByText('Another route')).toBeInTheDocument()
  })

})
