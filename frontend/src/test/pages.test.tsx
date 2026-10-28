import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ToolkitListPage } from '../pages/ToolkitListPage'
import { CheckoutPage } from '../pages/CheckoutPage'
import { AdminPage } from '../pages/AdminPage'
import { ToolkitDetailPage } from '../pages/ToolkitDetailPage'
import { HomePage } from '../pages/HomePage'
import { MyToolkitsPage, WalletPage } from '../pages/MePages'
import { setToken } from '../lib'
import { deferred, json, lesson, mockFetch, order, other, renderPage, settle, toolkit, user } from './helpers'

const list = (id: number, total = 1, page = 1) => ({ items: [toolkit(id)], total, page, pages: 3 })

describe('list URL debounce and pagination scopes', () => {
  it('debounces 400ms and cancels pending edits when filters change, without overwriting new URL params', async () => {
    mockFetch(({ path }) => path.startsWith('/toolkits?') ? json(list(1)) : undefined)
    const result = renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?q=old')
    await screen.findByText('Toolkit 1'); vi.useFakeTimers()
    const input = screen.getByLabelText('搜索工具包')
    fireEvent.change(input, { target: { value: 'stale' } })
    fireEvent.click(screen.getByRole('button', { name: '单细胞' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(result.router.state.location.search).toContain('category=')
    expect(new URLSearchParams(result.router.state.location.search).get('q')).toBe('old')
    fireEvent.change(input, { target: { value: 'new' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(399) })
    expect(new URLSearchParams(result.router.state.location.search).get('q')).toBe('old')
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(new URLSearchParams(result.router.state.location.search).get('q')).toBe('new')
    expect(new URLSearchParams(result.router.state.location.search).get('category')).toBe('单细胞')
  })
  it('back navigation and unmount clear stale debounce timers', async () => {
    const { calls } = mockFetch(({ path }) => path.startsWith('/toolkits?') ? json(list(1)) : undefined)
    const result = renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?q=first')
    await screen.findByText('Toolkit 1'); await result.navigate('/toolkits?q=second')
    await screen.findByText('Toolkit 1'); vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('搜索工具包'), { target: { value: 'pending' } })
    await result.navigate(-1)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(new URLSearchParams(result.router.state.location.search).get('q')).toBe('first')
    expect(screen.getByLabelText('搜索工具包')).toHaveValue('first')
    fireEvent.change(screen.getByLabelText('搜索工具包'), { target: { value: 'unmounted' } })
    const count = calls.length; result.unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(calls.length).toBe(count)
  })
  it('old results/catch/finally cannot populate or stop a newer pending filter', async () => {
    const old = deferred<Response>(), fresh = deferred<Response>()
    mockFetch(({ path }) => path.startsWith('/toolkits?') ? path.includes('q=new') ? fresh.promise : old.promise : undefined)
    const result = renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?q=old')
    await result.navigate('/toolkits?q=new')
    await settle(() => old.resolve(json(list(1))))
    expect(screen.queryByText('Toolkit 1')).not.toBeInTheDocument()
    expect(screen.queryByText('没有找到匹配的工具包')).not.toBeInTheDocument()
    await settle(() => fresh.resolve(json(list(2))))
    expect(screen.getByText('Toolkit 2')).toBeInTheDocument()
  })
  it('direct page-two entry respects the URL, reports only remaining pages and stops after page three', async () => {
    const { calls } = mockFetch(({ path }) => {
      if (!path.startsWith('/toolkits?')) return
      const page = Number(new URLSearchParams(path.split('?')[1]).get('page'))
      return json({ items: Array.from({ length: page === 2 ? 12 : 6 }, (_, index) => toolkit((page - 1) * 12 + index + 1)), total: 30, page, pages: 3 })
    })
    const result = renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?page=2')
    await screen.findByText('Toolkit 13')
    expect(screen.queryByText('Toolkit 1')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '加载更多（还有 6 个）' }))
    await screen.findByText('Toolkit 30')
    expect(screen.queryByRole('button', { name: /加载更多/ })).not.toBeInTheDocument()
    expect(calls.filter(call => call.path.startsWith('/toolkits?')).map(call => new URLSearchParams(call.path.split('?')[1]).get('page'))).toEqual(['2', '3'])
    expect(result.router.state.location.search).toBe('?page=2')
  })
  it('deduplicates load-more requests/pages and uses set(page), not repeated page params', async () => {
    const second = deferred<Response>()
    const { calls } = mockFetch(({ path }) => {
      if (path.startsWith('/toolkits?')) return new URLSearchParams(path.split('?')[1]).get('page') === '2' ? second.promise : json(list(1, 3))
    })
    renderPage(<ToolkitListPage />, '/toolkits', '/toolkits?page=1&page=8')
    await screen.findByText('Toolkit 1')
    const more = screen.getByRole('button', { name: /加载更多/ }); fireEvent.click(more); fireEvent.click(more)
    expect(calls.filter(x => x.path.includes('page=2'))).toHaveLength(1)
    expect(new URLSearchParams(calls.find(x => x.path.includes('page=2'))!.path.split('?')[1]).getAll('page')).toEqual(['2'])
    await settle(() => second.resolve(json({ items: [toolkit(1), toolkit(2)], total: 3, page: 2 })))
    expect(screen.getAllByText('Toolkit 1')).toHaveLength(1); expect(screen.getByText('Toolkit 2')).toBeInTheDocument()
  })
})

describe('checkout verification and route guards', () => {
  it('ignores a mismatched cached order and re-verifies on each ID', async () => {
    setToken('A'); const { calls } = mockFetch(({ path }) => path === '/me/orders' ? json([order(1), order(2)]) : undefined)
    const result = renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1', { order: order(2) })
    expect(await screen.findByText('ORDER-1')).toBeInTheDocument(); expect(screen.queryByText('ORDER-2')).not.toBeInTheDocument()
    await result.navigate('/checkout/2', { state: { order: order(1) } })
    expect(await screen.findByText('ORDER-2')).toBeInTheDocument(); expect(screen.queryByText('ORDER-1')).not.toBeInTheDocument()
    expect(calls.filter(x => x.path === '/me/orders').length).toBeGreaterThanOrEqual(2)
  })
  it('cache never authorizes an order missing from the current user list', async () => {
    setToken('A'); mockFetch(({ path }) => path === '/me/orders' ? json([]) : undefined)
    renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1', { order: order(1) })
    expect(await screen.findByText('未找到该订单，可能不属于当前账号。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /确认支付/ })).not.toBeInTheDocument()
  })
  it('a network failure is retryable, never mislabeled as missing', async () => {
    setToken('A'); let offline = true
    mockFetch(({ path }) => path === '/me/orders' ? offline ? Promise.reject(new Error('offline')) : json([order(1)]) : undefined)
    renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1')
    expect(await screen.findByRole('alert')).toHaveTextContent('订单读取失败：offline')
    expect(screen.queryByText('未找到该订单，可能不属于当前账号。')).not.toBeInTheDocument()
    offline = false; fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('ORDER-1')).toBeInTheDocument()
  })
  it('payment completion after navigation cannot redirect to the old course', async () => {
    setToken('A'); const payment = deferred<Response>()
    mockFetch(({ path }) => path === '/me/orders' ? json([order(1), order(2)]) : path === '/orders/1/pay' ? payment.promise : undefined)
    const result = renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1')
    await screen.findByText('ORDER-1'); fireEvent.click(screen.getByRole('button', { name: /确认支付/ }))
    await result.navigate('/checkout/2'); await screen.findByText('ORDER-2')
    await settle(() => payment.resolve(json({ ok: true })))
    expect(result.router.state.location.pathname).toBe('/checkout/2')
    expect(screen.getByRole('button', { name: /确认支付/ })).toBeEnabled()
  })
  it('an offline pending order can be cancelled but cannot be paid', async () => {
    setToken('A'); const offline = { ...order(1), canPay: false, toolkit: { ...order(1).toolkit, status: 'offline' } }
    const { calls } = mockFetch(({ path }) => path === '/me/orders' ? json([offline]) : path === '/orders/1/cancel' ? json({ ok: true }) : undefined)
    renderPage(<CheckoutPage />, '/checkout/:id', '/checkout/1')
    await screen.findByText('ORDER-1'); expect(screen.getByRole('button', { name: /确认支付/ })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('不能付款')
    fireEvent.click(screen.getByRole('button', { name: '取消订单' }))
    expect(await screen.findByText('订单状态：已取消')).toBeInTheDocument()
    expect(calls.some(x => x.path.endsWith('/pay'))).toBe(false)
  })
})

describe('permissions and administrative recovery', () => {
  it('waits for administrator restoration, permits retry after offline failure and has no refund action', async () => {
    setToken('A'); const restore = deferred<Response>(); let failed = false
    mockFetch(({ path }) => {
      if (path === '/auth/me') return failed ? json({ ...user, role: 'admin' }) : restore.promise
      if (path === '/admin/stats') return json({ users: 1, toolkits: 1, orders: 1, revenue: 10 })
      if (path === '/admin/toolkits') return json([toolkit(1)])
      if (path === '/admin/orders') return json([{ ...order(1), status: 'paid' }, { ...order(2), status: 'refunded' }])
    })
    const result = renderPage(<AdminPage />, '/admin', '/admin')
    expect(screen.getByRole('status')).toHaveTextContent('正在恢复管理员会话')
    expect(result.router.state.location.pathname).toBe('/admin')
    await settle(() => restore.reject(new Error('offline')))
    expect(screen.getByRole('alert')).toHaveTextContent('offline'); expect(result.router.state.location.pathname).toBe('/admin')
    failed = true; fireEvent.click(screen.getByRole('button', { name: '重试恢复' }))
    expect(await screen.findByText('管理中心')).toBeInTheDocument(); await screen.findByText('ORDER-1')
    expect(screen.queryByRole('button', { name: /退款/ })).not.toBeInTheDocument(); expect(screen.getByText('已退款')).toBeInTheDocument()
  })
  it('free anonymous start and paid preview links never create orders or open auth', async () => {
    mockFetch(({ path }) => path === '/toolkits/1' ? json(toolkit(1)) : path === '/toolkits/2' ? json({ ...toolkit(2, [lesson(21)]), price: 20, canLearn: false }) : undefined)
    const result = renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/1')
    await screen.findByText('Toolkit 1'); fireEvent.click(screen.getByRole('button', { name: '开始学习' }))
    expect(result.router.state.location.pathname).toBe('/toolkits/1/learn'); expect(result.openAuth).not.toHaveBeenCalled()
    await result.navigate('/toolkits/2'); await screen.findByText('Toolkit 2')
    fireEvent.click(screen.getByRole('link', { name: /免费试看/ }))
    expect(result.router.state.location.pathname).toBe('/toolkits/2/learn'); expect(result.openAuth).not.toHaveBeenCalled()
  })
  it('canLearn unlocks author/admin even when purchased is false, including offline purchases', async () => {
    setToken('A'); mockFetch(({ path }) => path === '/toolkits/1' ? json({ ...toolkit(1), price: 20, status: 'offline', purchased: false, canLearn: true }) : undefined)
    const result = renderPage(<ToolkitDetailPage />, '/toolkits/:id', '/toolkits/1')
    await screen.findByText('Toolkit 1'); fireEvent.click(screen.getByRole('button', { name: '开始学习' }))
    expect(result.router.state.location.pathname).toBe('/toolkits/1/learn')
  })
  it('delete mode offline keeps the card; only mode deleted removes it', async () => {
    setToken('A'); vi.spyOn(window, 'confirm').mockReturnValue(true); let mode = 'offline'
    mockFetch(({ path, options }) => path === '/me/toolkits' ? json([toolkit(1)]) : path === '/toolkits/1' && options.method === 'DELETE' ? json({ mode, message: 'done' }) : undefined)
    renderPage(<MyToolkitsPage />)
    await screen.findByText('Toolkit 1'); fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await screen.findByText('已下架'); expect(screen.getByText('Toolkit 1')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '删除' })).toBeEnabled())
    mode = 'deleted'; fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(await screen.findByText('还没有发布工具包')).toBeInTheDocument()
  })
  it('home requests featured filtering before pagination and never presents nonfeatured rows as featured', async () => {
    const { calls } = mockFetch(({ path }) => path.startsWith('/toolkits?') ? json({ items: [{ ...toolkit(1), featured: false }, { ...toolkit(2), featured: true }] }) : path === '/stats' ? json({ toolkits: 2, users: 1 }) : undefined)
    renderPage(<HomePage />)
    await screen.findByRole('heading', { name: 'Toolkit 2' })
    expect(screen.queryByText('Toolkit 1')).not.toBeInTheDocument()
    const query = new URLSearchParams(calls.find(x => x.path.startsWith('/toolkits?'))!.path.split('?')[1])
    expect(query.get('featured')).toBe('true'); expect(query.get('per_page')).toBe('6')
    expect(screen.queryByText('今日精选')).not.toBeInTheDocument()
  })
})

describe('demo wallet idempotency', () => {
  const wallet = { balance: 100, frozenBalance: 0, totalIncome: 0, transactions: [], demoEnabled: true, canRecharge: true, canWithdraw: true }
  it('uses one key for an ambiguous retry, disables double submission and clearly identifies fake funds', async () => {
    setToken('A'); const first = deferred<Response>(); let attempts = 0
    const { calls } = mockFetch(({ path }) => path === '/wallet' ? json(wallet) : path === '/wallet/recharge' ? ++attempts === 1 ? first.promise : json(user) : undefined)
    renderPage(<WalletPage />); await screen.findByRole('button', { name: '模拟充值' })
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟充值' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '模拟充值' }))
    const confirm = screen.getByRole('button', { name: '确认模拟操作' }); fireEvent.click(confirm); fireEvent.click(confirm)
    expect(attempts).toBe(1)
    await settle(() => first.reject(new TypeError('network result unknown')))
    expect(screen.getByLabelText('金额（元）')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '重试确认（同一请求）' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const operations = calls.filter(x => x.path === '/wallet/recharge')
    expect(operations).toHaveLength(2)
    const keys = operations.map(x => new Headers(x.options.headers).get('Idempotency-Key'))
    expect(keys[0]).toBeTruthy(); expect(keys[0]).toBe(keys[1]); expect(operations[0].options.body).toBe(operations[1].options.body)
    expect(screen.getByText(/不涉及真实资金或支付供应商/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟充值' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '模拟充值' })); fireEvent.click(screen.getByRole('button', { name: '确认模拟操作' }))
    await waitFor(() => expect(attempts).toBe(3))
    expect(new Headers(calls.filter(x => x.path === '/wallet/recharge')[2].options.headers).get('Idempotency-Key')).not.toBe(keys[0])
  })
  it('a submitted withdrawal survives navigation and retries only its persisted key and amount', async () => {
    setToken('A'); const pending = deferred<Response>(); let attempts = 0
    const { calls } = mockFetch(({ path }) => path === '/wallet' ? json(wallet) : path === '/wallet/withdraw' ? ++attempts === 1 ? pending.promise : json(user) : undefined)
    const result = renderPage(<WalletPage />, '/me/wallet', '/me/wallet')
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟提现' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '模拟提现' }))
    fireEvent.change(screen.getByLabelText('金额（元）'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '确认模拟操作' }))
    const first = calls.find(call => call.path === '/wallet/withdraw')!
    const saved = JSON.parse(localStorage.getItem('zhihe_wallet_pending_1')!)
    expect(saved).toEqual({ userId: 1, type: 'withdraw', amount: 10, key: new Headers(first.options.headers).get('Idempotency-Key') })
    await result.navigate('/another'); await result.navigate('/me/wallet')
    await screen.findByRole('button', { name: '继续确认' })
    expect(screen.getByRole('button', { name: '模拟充值' })).toBeDisabled()
    await settle(() => pending.resolve(json(user)))
    // A success ignored by the unmounted page cannot fabricate a new operation.
    expect(localStorage.getItem('zhihe_wallet_pending_1')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '继续确认' }))
    expect(screen.getByLabelText('金额（元）')).toHaveValue(10)
    expect(screen.getByLabelText('金额（元）')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '重试确认（同一请求）' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const sent = calls.filter(call => call.path === '/wallet/withdraw')
    expect(sent).toHaveLength(2)
    expect(sent[1].options.body).toBe(sent[0].options.body)
    expect(new Headers(sent[1].options.headers).get('Idempotency-Key')).toBe(saved.key)
    expect(localStorage.getItem('zhihe_wallet_pending_1')).toBeNull()
  })
  it('same-user re-login and provider reload retain the receipt, but another user never inherits it', async () => {
    setToken('A'); let identity = user, attempts = 0
    const { calls } = mockFetch(({ path }) => {
      if (path === '/wallet') return json(wallet)
      if (path === '/auth/login') return json({ token: `session-${identity.id}`, user: identity })
      if (path === '/auth/logout') return json({ ok: true })
      if (path === '/wallet/withdraw') return ++attempts === 1 ? Promise.reject(new TypeError('unknown response')) : json(identity)
    })
    const initial = renderPage(<WalletPage />, '/me/wallet', '/me/wallet', undefined, true)
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟提现' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '模拟提现' }))
    fireEvent.change(screen.getByLabelText('金额（元）'), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: '确认模拟操作' }))
    await screen.findByRole('button', { name: '重试确认（同一请求）' })
    const saved = JSON.parse(localStorage.getItem('zhihe_wallet_pending_1')!)
    fireEvent.click(screen.getByRole('button', { name: 'Logout session' }))
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    await screen.findByRole('button', { name: '继续确认' })
    expect(JSON.parse(localStorage.getItem('zhihe_wallet_pending_1')!).key).toBe(saved.key)
    initial.unmount()
    renderPage(<WalletPage />, '/me/wallet', '/me/wallet', undefined, true)
    await screen.findByRole('button', { name: '继续确认' })
    identity = other
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    await screen.findByText('Other')
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟提现' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: '继续确认' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模拟提现' }))
    expect(screen.getByLabelText('金额（元）')).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '确认模拟操作' }))
    await waitFor(() => expect(attempts).toBe(2))
    const second = calls.filter(call => call.path === '/wallet/withdraw')[1]
    expect(new Headers(second.options.headers).get('Idempotency-Key')).not.toBe(saved.key)
    expect(JSON.parse(localStorage.getItem('zhihe_wallet_pending_1')!).key).toBe(saved.key)
  })
  it.each([
    { userId: 2 }, { key: 'untrusted-not-a-uuid' }, { type: 'refund' }, { amount: 10.001 },
  ])('does not trust malformed or mismatched persisted wallet receipts: %j', async invalid => {
    setToken('A')
    localStorage.setItem('zhihe_wallet_pending_1', JSON.stringify({ userId: 1, key: '00000000-0000-4000-8000-000000000001', type: 'withdraw', amount: 10, ...invalid }))
    mockFetch(({ path }) => path === '/wallet' ? json(wallet) : undefined)
    renderPage(<WalletPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟提现' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: '继续确认' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模拟提现' }))
    expect(screen.getByLabelText('金额（元）')).toBeEnabled()
    expect(screen.getByRole('button', { name: '确认模拟操作' })).toBeVisible()
  })
  it('never sends a wallet operation if its retry receipt cannot be persisted', async () => {
    setToken('A'); const { calls } = mockFetch(({ path }) => path === '/wallet' ? json(wallet) : undefined)
    renderPage(<WalletPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: '模拟提现' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '模拟提现' }))
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => { throw new Error('storage disabled') })
    fireEvent.click(screen.getByRole('button', { name: '确认模拟操作' }))
    expect(calls.some(call => call.path === '/wallet/withdraw')).toBe(false)
    expect(screen.getByRole('button', { name: '确认模拟操作' })).toBeEnabled()
  })
  it('capabilities disable demo operations in production/unopened wallets', async () => {
    setToken('A'); mockFetch(({ path }) => path === '/wallet' ? json({ ...wallet, demoEnabled: false, canRecharge: false, canWithdraw: false }) : undefined)
    renderPage(<WalletPage />)
    await screen.findByText(/当前环境未开放模拟操作/)
    expect(screen.getByRole('button', { name: '充值未开放' })).toBeDisabled(); expect(screen.getByRole('button', { name: '提现未开放' })).toBeDisabled()
  })
})
