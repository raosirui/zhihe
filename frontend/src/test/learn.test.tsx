import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { LearnPage } from '../pages/LearnPage'
import { ProtectedImage } from '../components/ProtectedImage'
import { setToken } from '../lib'
import { deferred, json, lesson, mockFetch, other, renderPage, settle, toolkit } from './helpers'

const course = toolkit(1, [lesson(11, 'First lesson'), lesson(12, 'Second lesson'), lesson(13, 'Third lesson')])

describe('learning space', () => {
  it.each([[], undefined])('renders an explicit empty state for empty or missing lessons', async lessons => {
    mockFetch(({ path }) => path === '/toolkits/1' ? json({ ...course, lessons }) : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    expect(await screen.findByText('暂无课程')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '本节完成' })).not.toBeInTheDocument()
  })
  it('next saves the current lesson before navigation and every lesson has a complete button', async () => {
    setToken('A'); const pending = deferred<Response>()
    const { calls } = mockFetch(({ path }) => path === '/toolkits/1' ? json(course) : path === '/lessons/11/complete' ? pending.promise : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    expect(await screen.findByRole('heading', { name: 'First lesson' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '本节完成' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一节' }))
    expect(screen.getByRole('heading', { name: 'First lesson' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存中…' })).toBeDisabled()
    await settle(() => pending.resolve(json({ ok: true })))
    expect(screen.getByRole('heading', { name: 'Second lesson' })).toBeInTheDocument()
    expect(calls.filter(x => x.path.includes('/complete'))).toHaveLength(1)
    expect(calls.find(x => x.path.includes('/complete'))?.path).toBe('/lessons/11/complete')
  })
  it('a delayed initial progress read retains newly confirmed completions and historical progress', async () => {
    setToken('A'); const progress = deferred<Response>()
    mockFetch(({ path }) => path === '/toolkits/1' ? json(course) : path.startsWith('/me/lesson-progress') ? progress.promise : path === '/lessons/11/complete' ? json({ ok: true }) : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    await screen.findByRole('heading', { name: 'First lesson' })
    fireEvent.click(screen.getByRole('button', { name: '本节完成' }))
    await screen.findByRole('button', { name: '本节已完成' })
    await settle(() => progress.resolve(json([13])))
    expect(screen.getByRole('button', { name: '本节已完成' })).toBeVisible()
    expect(screen.getByText('已完成')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /Third lesson/ }))
    expect(screen.getByRole('button', { name: '本节已完成' })).toBeVisible()
  })
  it('a non-first preview cannot navigate backward into a locked lesson or lose its directory', async () => {
    mockFetch(({ path }) => path === '/toolkits/1' ? json({ ...course, lessons: [{ ...lesson(11, 'Locked first'), locked: true }, lesson(12, 'Available preview')] }) : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn?lesson=12')
    await screen.findByRole('heading', { name: 'Available preview' })
    expect(screen.getByRole('button', { name: '上一节' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '上一节' }))
    expect(screen.getByRole('heading', { name: 'Available preview' })).toBeVisible()
    expect(screen.getByRole('button', { name: /Locked first/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: '本节完成' })).toBeEnabled()
    expect(screen.queryByText('课程尚未解锁')).not.toBeInTheDocument()
  })
  it('manual selection cannot be moved by an old completion callback', async () => {
    setToken('A'); const pending = deferred<Response>()
    mockFetch(({ path }) => path === '/toolkits/1' ? json(course) : path === '/lessons/11/complete' ? pending.promise : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    await screen.findByRole('heading', { name: 'First lesson' })
    fireEvent.click(screen.getByRole('button', { name: '下一节' }))
    fireEvent.click(screen.getByRole('button', { name: /Third lesson/ }))
    await settle(() => pending.resolve(json({ ok: true })))
    expect(screen.getByRole('heading', { name: 'Third lesson' })).toBeInTheDocument()
  })
  it('out-of-order course responses and old completions never affect a new route', async () => {
    setToken('A'); const courses: ReturnType<typeof deferred<Response>>[] = [], pending = deferred<Response>()
    mockFetch(({ path }) => {
      if (path === '/toolkits/1') { const d = deferred<Response>(); courses.push(d); return d.promise }
      if (path === '/toolkits/2') return json(toolkit(2, [lesson(21, 'New course')]))
      if (path === '/lessons/11/complete') return pending.promise
    })
    const result = renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    await waitFor(() => expect(courses.length).toBeGreaterThan(0))
    await settle(() => courses[courses.length - 1].resolve(json(course)))
    fireEvent.click(screen.getByRole('button', { name: '下一节' }))
    await result.navigate('/toolkits/2/learn')
    await screen.findByRole('heading', { name: 'New course' })
    await settle(() => { courses[0].resolve(json(course)); pending.resolve(json({ ok: true })) })
    expect(screen.getByRole('heading', { name: 'New course' })).toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
  })
  it('session changes clear private content/progress/busy even if the new request is still pending', async () => {
    setToken('A'); const pendingProgress = deferred<Response>(), nextCourse = deferred<Response>(); let newSession = false
    mockFetch(({ path }) => {
      if (path === '/auth/login') { newSession = true; return json({ token: 'B', user: other }) }
      if (path === '/toolkits/1') return newSession ? nextCourse.promise : json(course)
      if (path.startsWith('/me/lesson-progress')) return newSession ? json([]) : pendingProgress.promise
    })
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn', undefined, true)
    await screen.findByRole('heading', { name: 'First lesson' })
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    await screen.findByText('Other')
    expect(screen.queryByRole('heading', { name: 'First lesson' })).not.toBeInTheDocument()
    await settle(() => { pendingProgress.resolve(json([11])); nextCourse.resolve(json(course)) })
    await screen.findByRole('heading', { name: 'First lesson' })
    expect(screen.queryByText('已完成')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '本节完成' })).toBeEnabled()
  })
  it('anonymous free/preview learning advances without inventing persisted progress', async () => {
    const { calls } = mockFetch(({ path }) => path === '/toolkits/1' ? json(course) : undefined)
    renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    await screen.findByRole('heading', { name: 'First lesson' })
    fireEvent.click(screen.getByRole('button', { name: '下一节' }))
    expect(screen.getByRole('heading', { name: 'Second lesson' })).toBeInTheDocument()
    expect(calls.some(x => x.path.includes('complete') || x.path.includes('lesson-progress'))).toBe(false)
    expect(screen.queryByText('已完成')).not.toBeInTheDocument()
  })
  it('renders sanitized markdown and removes executable image/links', async () => {
    mockFetch(({ path }) => path === '/toolkits/1' ? json(toolkit(1, [{ ...lesson(11), content: '[evil](javascript:alert(1))\n\n<script>alert(1)</script>\n\n**Safe content**' }])) : undefined)
    const result = renderPage(<LearnPage />, '/toolkits/:id/learn', '/toolkits/1/learn')
    expect(await screen.findByText('Safe content')).toBeInTheDocument()
    expect(result.container.querySelector('script')).toBeNull()
    expect(screen.getByText('evil')).not.toHaveAttribute('href', 'javascript:alert(1)')
  })
})

describe('protected images', () => {
  it('fetches same-origin lesson assets with header credentials, strips query JWT and revokes blob on cleanup', async () => {
    setToken('A'); const create = vi.fn(() => 'blob:protected'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    const { calls } = mockFetch(({ path }) => path === '/lesson-asset/image.png' ? new Response('image') : undefined)
    const result = renderPage(<ProtectedImage src="/api/lesson-asset/image.png?token=bad" alt="Course image" />)
    await screen.findByRole('img', { name: 'Course image' })
    const imageCall = calls.find(x => x.path.startsWith('/lesson-asset'))!
    expect(imageCall.url).toBe('/api/lesson-asset/image.png')
    expect(new Headers(imageCall.options.headers).get('Authorization')).toBe('Bearer A')
    expect(imageCall.options.signal).toBeInstanceOf(AbortSignal)
    result.unmount(); expect(revoke).toHaveBeenCalledWith('blob:protected')
  })
  it('does not attach credentials or auth-fetch an external URL', async () => {
    setToken('A'); const { calls } = mockFetch(() => undefined)
    renderPage(<ProtectedImage src="https://external.example/api/lesson-asset/file.png" alt="External" />)
    expect(screen.getByRole('img', { name: 'External' })).toHaveAttribute('src', 'https://external.example/api/lesson-asset/file.png')
    await screen.findByRole('img', { name: 'External' })
    expect(calls.some(x => x.url.includes('external.example'))).toBe(false)
  })
  it('allows anonymous protected preview asset fetching and discards stale blobs', async () => {
    const pending = deferred<Response>(), create = vi.fn(() => 'blob:preview'), revoke = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
    const { calls } = mockFetch(({ path }) => path.startsWith('/lesson-asset') ? pending.promise : undefined)
    const result = renderPage(<ProtectedImage src="/api/lesson-asset/preview.png" />)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    expect(new Headers(calls[0].options.headers).has('Authorization')).toBe(false)
    result.unmount(); await settle(() => pending.resolve(new Response('image')))
    expect(create).not.toHaveBeenCalled()
  })
})
