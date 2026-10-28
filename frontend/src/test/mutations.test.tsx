import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
    fireEvent.change(screen.getByLabelText('账号'), { target: { value: 'legacy' } }); fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'Old1234' } })
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
  it('batch reports created/failed/skipped independently from actual response rows', async () => {
    setToken('A')
    mockFetch(({ path }) => path === '/batch/prompt' ? json({ text: 'safe prompt' }) : path === '/batch/upload' ? json({ total: 3, created: 1, failed: 1, skipped: 1, results: [{ index: 1, title: 'Created title', status: 'created', toolkitId: 1 }, { index: 2, title: 'Failed title', status: 'failed', error: 'bad input' }, { index: 3, title: 'Skipped title', status: 'skipped' }] }) : undefined)
    const result = renderPage(<BatchPublishPage />)
    await screen.findByText('开始批量上传')
    const fileInput = result.container.querySelector('input[type=file]')!
    fireEvent.change(fileInput, { target: { files: [new File(['zip'], 'safe.zip', { type: 'application/zip' })] } })
    fireEvent.click(screen.getByRole('button', { name: '开始批量上传' }))
    expect(await screen.findByText('共 3 条 · 创建 1 · 失败 1 · 跳过 1')).toBeInTheDocument()
    expect(screen.getByText('Skipped title')).toBeInTheDocument(); expect(screen.getByText('bad input')).toBeInTheDocument()
  })
})
