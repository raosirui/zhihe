import { StrictMode, type ReactNode } from 'react'
import { act, render } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider, type RouteObject } from 'react-router-dom'
import { vi } from 'vitest'
import { AuthProvider, useAuth } from '../AuthContext'
import type { Lesson, Order, Toolkit, User } from '../types'

export const user: User = { id: 1, username: 'buyer', nickname: 'Buyer', role: 'user', bio: '', balance: 100, frozenBalance: 0 }
export const other: User = { ...user, id: 2, username: 'other', nickname: 'Other' }
export const lesson = (id: number, title = `Lesson ${id}`): Lesson => ({ id, title, duration: 10, sortOrder: id, isPreview: true, locked: false, content: `Content ${id}`, image: null })
export const toolkit = (id: number, lessons: Lesson[] = [lesson(id * 10)]): Toolkit => ({ id, title: `Toolkit ${id}`, slug: `toolkit-${id}`, summary: 'Summary', category: '生信分析', level: '入门', price: 0, author: { id: 9, nickname: 'Author' }, downloads: 0, rating: 0, featured: false, tags: [], status: 'published', purchased: false, canLearn: true, canDownload: true, favorited: false, lessonCount: lessons.length, lessons, createdAt: '2026-01-01' })
export const order = (id: number, toolkitId = id): Order => ({ id, orderNo: `ORDER-${id}`, amount: 10, commission: 2, status: 'pending', toolkit: { id: toolkitId, title: `Order Toolkit ${toolkitId}`, status: 'published' }, canPay: true, seller: 'Author', createdAt: '2026-01-01' })
export function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
export const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
export type FetchCall = { url: string; path: string; options: RequestInit }
export function mockFetch(handler: (call: FetchCall) => Response | Promise<Response> | undefined) {
  const calls: FetchCall[] = []
  const fetch = vi.fn(async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = String(input), path = url.replace(/^\/api/, '')
    const call = { url, path, options }; calls.push(call)
    const result = handler(call)
    if (result) return await result
    if (path === '/auth/me') return json(user)
    if (path === '/wallet') return json({ balance: user.balance, frozenBalance: 0, totalIncome: 0, transactions: [], demoEnabled: true, canPay: true, canRecharge: true, canWithdraw: true })
    if (path.startsWith('/me/lesson-progress')) return json([])
    throw new Error(`Unhandled mock API: ${path}`)
  })
  vi.stubGlobal('fetch', fetch)
  return { calls, fetch }
}
export function AuthControls() {
  const auth = useAuth()
  return <div><p data-testid="identity">{auth.user?.nickname ?? 'Anonymous'}</p><p data-testid="auth-loading">{String(auth.loading)}</p>{auth.restoreError && <p role="alert">{auth.restoreError}</p>}<button onClick={() => void auth.login('other', 'secret').catch(() => {})}>Switch session</button><button onClick={() => void auth.register('new-account', 'new-password', 'New').catch(() => {})}>Register session</button><button onClick={auth.logout}>Logout session</button><button onClick={() => void auth.refresh().catch(() => {})}>Refresh session</button></div>
}
export function renderRoutes(routes: RouteObject[], initial = '/', state?: unknown, controls = false) {
  const openAuth = vi.fn()
  const router = createMemoryRouter([{ element: <>{controls && <AuthControls />}<Outlet context={{ openAuth }} /></>, children: routes }], { initialEntries: [{ pathname: initial.split('?')[0], search: initial.includes('?') ? `?${initial.split('?')[1]}` : '', state }] })
  const result = render(<StrictMode><AuthProvider><RouterProvider router={router} /></AuthProvider></StrictMode>)
  return { ...result, router, openAuth, navigate: async (to: string | number, options?: { state?: unknown }) => { await act(async () => { if (typeof to === 'number') await router.navigate(to); else await router.navigate(to, options) }) } }
}
export function renderPage(element: ReactNode, path = '/', initial = path, state?: unknown, controls = false) {
  return renderRoutes([{ path, element }, { path: '*', element: <p>Another route</p> }], initial, state, controls)
}
export async function settle(fn: () => void) { await act(async () => { fn(); await Promise.resolve() }) }
