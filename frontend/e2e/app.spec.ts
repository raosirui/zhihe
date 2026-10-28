import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test'
import { fixtures, type Fixtures } from './isolation'

type Account = Fixtures['users']['buyer']
const test = base.extend<{ qa: Fixtures }>({
  qa: [async ({ request }, use) => {
    const original = fixtures()
    const ready = await request.get('/api/__qa__/ready')
    expect(ready.ok()).toBeTruthy(); expect((await ready.json()).runId).toBe(original.runId)
    const reset = await request.post('/api/__qa__/reset', { headers: { 'X-Zhihe-QA': original.runId } })
    expect(reset.ok(), 'Only the coordinator-owned isolated reset endpoint may prepare fixtures').toBeTruthy()
    const fresh = await reset.json() as Fixtures
    expect(fresh.runId).toBe(original.runId); expect(new URL(fresh.origin).origin).toBe(new URL(original.origin).origin)
    await use(fresh)
  }, { auto: true }],
})
async function loginUI(page: Page, account: Account) {
  await page.goto('/')
  await page.getByRole('button', { name: '登录 / 注册', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '登录知核' })
  await dialog.getByLabel('账号', { exact: true }).fill(account.username)
  await dialog.getByLabel('密码', { exact: true }).fill(account.password)
  expect(await dialog.getByLabel('密码', { exact: true }).evaluate((input: HTMLInputElement) => input.checkValidity())).toBe(true)
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/auth/login' && r.request().method() === 'POST')
  await dialog.getByRole('button', { name: '登录', exact: true }).click()
  expect((await response).ok()).toBeTruthy()
  await expect(dialog).not.toBeVisible()
}
async function loginAPI(request: APIRequestContext, account: Account) {
  const response = await request.post('/api/auth/login', { data: account })
  expect(response.ok()).toBeTruthy()
  return { Authorization: `Bearer ${(await response.json()).token}` }
}
async function getJSON(request: APIRequestContext, path: string, headers: Record<string, string>) {
  const response = await request.get(`/api${path}`, { headers })
  expect(response.ok()).toBeTruthy(); return response.json()
}

test('anonymous free course and paid preview are real learning entry points without creating orders', async ({ page, qa }) => {
  const createdOrders: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/orders') createdOrders.push(request.url()) })
  await page.goto(`/toolkits/${qa.toolkits.free}`)
  await page.getByRole('button', { name: '开始学习', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/toolkits/${qa.toolkits.free}/learn`))
  await expect(page.locator('article h1')).toBeVisible()
  await page.getByRole('button', { name: '下一节', exact: true }).click()
  await expect(page.locator('article')).toContainText('第 2 节')
  await expect(page.locator('article')).not.toContainText('已完成')
  await page.goto(`/toolkits/${qa.toolkits.paid}`)
  await page.getByRole('link', { name: '免费试看', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/toolkits/${qa.toolkits.paid}/learn`))
  await expect(page.locator('article h1')).toBeVisible()
  await expect(page.getByRole('button', { name: '下一节', exact: true })).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(createdOrders).toEqual([])
})

test('actual short legacy password passes browser HTML validation and authenticates', async ({ page, qa }) => {
  expect(qa.users.legacy.password.length).toBeGreaterThanOrEqual(6)
  expect(qa.users.legacy.password.length).toBeLessThan(8)
  await loginUI(page, qa.users.legacy)
  await page.goto('/me/orders')
  await expect(page.getByRole('heading', { name: '我的订单', exact: true })).toBeVisible()
  const token = await page.evaluate(() => localStorage.getItem('zhihe_token'))
  expect(token).toBeTruthy()
})

test('empty real course renders a stable empty state', async ({ page, qa }) => {
  await page.goto(`/toolkits/${qa.toolkits.empty}/learn`)
  await expect(page.getByText('暂无课程', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '本节完成', exact: true })).toHaveCount(0)
})

test('real purchase, wallet settlement, each lesson completion and offline purchased learning', async ({ page, request, qa }) => {
  await loginUI(page, qa.users.buyer)
  const token = await page.evaluate(() => localStorage.getItem('zhihe_token'))
  const headers = { Authorization: `Bearer ${token}` }
  const walletBefore = await getJSON(request, '/wallet', headers)
  await page.goto(`/toolkits/${qa.toolkits.paid}`)
  const creation = page.waitForResponse(r => new URL(r.url()).pathname === '/api/orders' && r.request().method() === 'POST')
  await page.getByRole('button', { name: '立即购买', exact: true }).click()
  const created = await creation; expect(created.ok()).toBeTruthy()
  const order = await created.json()
  await expect(page).toHaveURL(new RegExp(`/checkout/${order.id}$`))
  await expect(page.getByText(order.orderNo, { exact: true })).toBeVisible()
  const payButton = page.getByRole('button', { name: /^确认支付/ })
  await expect(payButton).toBeEnabled()
  const payment = page.waitForResponse(r => new URL(r.url()).pathname === `/api/orders/${order.id}/pay`)
  await payButton.click(); expect((await payment).ok()).toBeTruthy()
  await expect(page).toHaveURL(new RegExp(`/toolkits/${qa.toolkits.paid}/learn`))
  await expect(page.locator('article h1')).toBeVisible()
  const completion = page.waitForResponse(r => new URL(r.url()).pathname === `/api/lessons/${qa.lessons.paidPreview}/complete`)
  await page.getByRole('button', { name: '下一节', exact: true }).click()
  expect((await completion).ok()).toBeTruthy()
  await expect(page.locator('article')).toContainText('第 2 节')
  const lastCompletion = page.waitForResponse(r => new URL(r.url()).pathname === `/api/lessons/${qa.lessons.paidLocked}/complete`)
  await page.getByRole('button', { name: '本节完成', exact: true }).click()
  expect((await lastCompletion).ok()).toBeTruthy()
  await expect(page.locator('article')).toContainText('已完成')
  const progress = await getJSON(request, `/me/lesson-progress?toolkit_id=${qa.toolkits.paid}`, headers)
  expect(progress).toEqual(expect.arrayContaining([qa.lessons.paidPreview, qa.lessons.paidLocked]))
  const walletAfter = await getJSON(request, '/wallet', headers)
  expect(walletAfter.balance).toBeCloseTo(walletBefore.balance - order.amount, 2)
  expect((await getJSON(request, '/me/orders', headers)).find((row: { id: number }) => row.id === order.id).status).toBe('paid')
  const author = await loginAPI(request, qa.users.author)
  const offline = await request.put(`/api/toolkits/${qa.toolkits.paid}/status`, { headers: author, data: { status: 'offline' } })
  expect(offline.ok()).toBeTruthy()
  await page.goto(`/toolkits/${qa.toolkits.paid}`)
  await page.getByRole('button', { name: '开始学习', exact: true }).click()
  await expect(page.locator('article h1')).toBeVisible()
})

test('real offline pending order is not payable but remains cancellable', async ({ page, request, qa }) => {
  const buyer = await loginAPI(request, qa.users.buyer)
  const creation = await request.post('/api/orders', { headers: buyer, data: { toolkitId: qa.toolkits.paidNoPreview } })
  expect(creation.ok()).toBeTruthy(); const order = await creation.json()
  const author = await loginAPI(request, qa.users.author)
  const offline = await request.put(`/api/toolkits/${qa.toolkits.paidNoPreview}/status`, { headers: author, data: { status: 'offline' } })
  expect(offline.ok()).toBeTruthy()
  await loginUI(page, qa.users.buyer)
  await page.goto(`/checkout/${order.id}`)
  await expect(page.getByRole('alert')).toContainText('不能付款')
  await expect(page.getByRole('button', { name: /^确认支付/ })).toBeDisabled()
  const cancellation = page.waitForResponse(r => new URL(r.url()).pathname === `/api/orders/${order.id}/cancel`)
  await page.getByRole('button', { name: '取消订单', exact: true }).click()
  expect((await cancellation).ok()).toBeTruthy()
  await expect(page.getByText('订单状态：已取消', { exact: true })).toBeVisible()
})

test('admin UI offers no refund and real refund request returns 403 with no financial/entitlement side effects', async ({ page, request, qa }) => {
  const buyer = await loginAPI(request, qa.users.buyer), author = await loginAPI(request, qa.users.author)
  const creation = await request.post('/api/orders', { headers: buyer, data: { toolkitId: qa.toolkits.paid } })
  expect(creation.ok()).toBeTruthy(); const order = await creation.json()
  expect((await request.post(`/api/orders/${order.id}/pay`, { headers: buyer })).ok()).toBeTruthy()
  await loginUI(page, qa.users.admin)
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: '管理中心', exact: true })).toBeVisible()
  await expect(page.getByText(order.orderNo, { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /退款/ })).toHaveCount(0)
  const adminToken = await page.evaluate(() => localStorage.getItem('zhihe_token'))
  const snapshot = async () => ({ buyerWallet: await getJSON(request, '/wallet', buyer), authorWallet: await getJSON(request, '/wallet', author), orders: await getJSON(request, '/me/orders', buyer), entitlement: await getJSON(request, `/toolkits/${qa.toolkits.paid}`, buyer) })
  const before = await snapshot()
  const refund = await request.post(`/api/admin/orders/${order.id}/refund`, { headers: { Authorization: `Bearer ${adminToken}` } })
  expect(refund.status()).toBe(403)
  expect((await refund.json()).code).toBe('REFUNDS_DISABLED')
  expect(await snapshot()).toEqual(before)
})

test('actual admin page keeps its route during deferred restore failure and retries the authenticated API', async ({ page, request, qa }) => {
  const headers = await loginAPI(request, qa.users.admin), token = headers.Authorization.slice('Bearer '.length)
  await page.addInitScript(value => localStorage.setItem('zhihe_token', value), token)
  let release!: () => void, first = true
  const held = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/auth/me', async route => {
    if (!first) { await route.continue(); return }
    first = false; await held
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: 'Deferred test network failure' }) })
  })
  await page.goto('/admin')
  await expect(page.getByText('正在恢复管理员会话…', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/admin$/)
  release()
  await expect(page.getByText('会话恢复失败：Deferred test network failure', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/admin$/)
  await page.getByRole('button', { name: '重试恢复', exact: true }).last().click()
  await expect(page.getByRole('heading', { name: '管理中心', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /退款/ })).toHaveCount(0)
})

test('browser search debounce and a delayed old list response do not override a new filter', async ({ page }) => {
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  const row = (id: number, title: string) => ({ id, title, summary: 'Test fixture row', slug: title, category: '生信分析', level: '入门', price: 0, author: { id: 1, nickname: 'Author' }, downloads: 0, rating: 0, featured: false, tags: [], status: 'published', purchased: false, favorited: false, lessonCount: 1 })
  await page.route('**/api/toolkits?**', async route => {
    const params = new URL(route.request().url()).searchParams
    if (params.get('q') === 'old') await held
    const fresh = params.get('q') === 'new'
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [row(fresh ? 2 : 1, fresh ? 'New browser result' : 'Old browser result')], total: 1, page: 1, pages: 1 }) }).catch(() => {})
  })
  await page.goto('/toolkits?q=old')
  await page.getByLabel('搜索工具包', { exact: true }).fill('new')
  await expect(page).toHaveURL(/q=new/)
  await expect(page.getByRole('heading', { name: 'New browser result', exact: true })).toBeVisible()
  release()
  await expect(page.getByRole('heading', { name: 'Old browser result', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '单细胞', exact: true }).click()
  await expect(page).toHaveURL(/category=/)
  await expect(page.getByLabel('搜索工具包', { exact: true })).toHaveValue('new')
})

test('demo wallet UI sends idempotency key and backend rejects same key with a different amount', async ({ page, request, qa }) => {
  await loginUI(page, qa.users.buyer)
  await page.goto('/me/wallet')
  await expect(page.getByText(/不涉及真实资金或支付供应商/)).toBeVisible()
  await page.getByRole('button', { name: '模拟充值', exact: true }).click()
  await page.getByLabel('金额（元）', { exact: true }).fill('10')
  const operation = page.waitForRequest(r => new URL(r.url()).pathname === '/api/wallet/recharge')
  const result = page.waitForResponse(r => new URL(r.url()).pathname === '/api/wallet/recharge')
  await page.getByRole('button', { name: '确认模拟操作', exact: true }).click()
  const sent = await operation, response = await result
  expect(response.ok()).toBeTruthy(); const key = sent.headers()['idempotency-key']
  expect(key).toBeTruthy()
  const token = await page.evaluate(() => localStorage.getItem('zhihe_token')), headers = { Authorization: `Bearer ${token}`, 'Idempotency-Key': key }
  const walletBefore = await getJSON(request, '/wallet', headers)
  expect((await request.post('/api/wallet/recharge', { headers, data: { amount: 10 } })).ok()).toBeTruthy()
  expect((await request.post('/api/wallet/recharge', { headers, data: { amount: 11 } })).status()).toBe(409)
  expect(await getJSON(request, '/wallet', headers)).toEqual(walletBefore)
})
