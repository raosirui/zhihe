import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { toast } from 'sonner'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { api, downloadAuthed, getToken, setToken } from '../lib'
import { AuthModal } from '../components/AuthModal'
import { AuthControls, deferred, json, mockFetch, other, renderPage, settle, user } from './helpers'

function ModalHarness() {
  const [open, setOpen] = useState(true)
  return <><AuthControls /><AuthModal open={open} onClose={() => setOpen(false)} /></>
}

describe('authentication and scoped transport', () => {
  it('an old 401 cannot clear a newer token, including A -> B -> A generations', async () => {
    const pending = deferred<Response>()
    mockFetch(() => pending.promise)
    setToken('A'); const request = api('/private').catch(error => error)
    setToken('B'); setToken('A')
    pending.resolve(json({ message: 'Expired old request' }, 401))
    expect(await request).toMatchObject({ status: 401 }); expect(getToken()).toBe('A')
  })
  it('current authenticated 401 expires but ordinary network failure never logs out', async () => {
    setToken('A'); mockFetch(() => Promise.reject(new TypeError('offline')))
    await expect(api('/private')).rejects.toThrow('offline'); expect(getToken()).toBe('A')
    mockFetch(() => json({ message: 'expired' }, 401))
    await expect(api('/private')).rejects.toThrow('expired'); expect(getToken()).toBeNull()
  })
  it.each(['/auth/login', '/auth/register'])('credential 401 from %s preserves an existing session and sends no Bearer', async path => {
    setToken('valid-other-session')
    const { calls } = mockFetch(() => json({ message: 'bad credentials' }, 401))
    await expect(api(path, { method: 'POST', body: '{}' })).rejects.toThrow('bad credentials')
    expect(getToken()).toBe('valid-other-session'); expect(new Headers(calls[0].options.headers).has('Authorization')).toBe(false)
  })
  it('preserves FormData and AbortSignal rather than setting JSON multipart headers', async () => {
    const { calls } = mockFetch(() => json({ ok: true })), controller = new AbortController(), body = new FormData()
    body.append('example', new Blob(['safe']), 'file.txt')
    await api('/upload', { method: 'POST', body, signal: controller.signal })
    expect(calls[0].options.body).toBe(body); expect(calls[0].options.signal).toBe(controller.signal)
    expect(new Headers(calls[0].options.headers).has('Content-Type')).toBe(false)
  })
  it('download 401 shares expiration rules and a stale successful blob cannot download', async () => {
    setToken('A'); mockFetch(() => json({ message: 'expired download' }, 401))
    await expect(downloadAuthed('/file', 'file')).rejects.toThrow('expired download'); expect(getToken()).toBeNull()
    const pending = deferred<Response>(); const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    mockFetch(() => pending.promise); setToken('A')
    const request = downloadAuthed('/file', 'file').catch(error => error); setToken('B')
    pending.resolve(new Response('blob'))
    expect((await request).name).toBe('AbortError'); expect(click).not.toHaveBeenCalled()
  })
  it('StrictMode restore fails offline without dropping the token and can retry', async () => {
    setToken('A'); let offline = true
    mockFetch(({ path }) => path === '/auth/me' ? offline ? Promise.reject(new Error('Network unavailable')) : json(user) : undefined)
    renderPage(<AuthControls />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable'); expect(getToken()).toBe('A')
    expect(screen.getByTestId('auth-loading')).toHaveTextContent('false')
    offline = false; fireEvent.click(screen.getByRole('button', { name: 'Refresh session' }))
    expect(await screen.findByText('Buyer')).toBeInTheDocument(); expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('restore success and failure arriving after login cannot replace the new identity', async () => {
    setToken('A'); const restores: ReturnType<typeof deferred<Response>>[] = []
    mockFetch(({ path }) => {
      if (path === '/auth/me') { const pending = deferred<Response>(); restores.push(pending); return pending.promise }
      if (path === '/auth/login') return json({ token: 'B', user: other })
    })
    renderPage(<AuthControls />)
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    expect(await screen.findByText('Other')).toBeInTheDocument()
    await settle(() => { restores[0].resolve(json(user)); restores[1].resolve(json({ message: 'old expired' }, 401)) })
    expect(screen.getByTestId('identity')).toHaveTextContent('Other'); expect(getToken()).toBe('B')
    expect(screen.getByTestId('auth-loading')).toHaveTextContent('false')
  })
  it('an aborted restore 401 arriving before the new login success cannot invalidate that login', async () => {
    setToken('A'); const restores: ReturnType<typeof deferred<Response>>[] = [], login = deferred<Response>()
    const { calls } = mockFetch(({ path }) => {
      if (path === '/auth/me') { const pending = deferred<Response>(); restores.push(pending); return pending.promise }
      if (path === '/auth/login') return login.promise
    })
    renderPage(<AuthControls />)
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' }))
    expect(calls.filter(call => call.path === '/auth/me').every(call => call.options.signal?.aborted)).toBe(true)
    await settle(() => restores.forEach(pending => pending.resolve(json({ message: 'old expired restore' }, 401))))
    expect(getToken()).toBe('A')
    await settle(() => login.resolve(json({ token: 'B', user: other })))
    expect(screen.getByTestId('identity')).toHaveTextContent('Other')
    expect(getToken()).toBe('B')
  })
  it('an aborted download or invalid caller cannot expire an unchanged current token', async () => {
    setToken('A'); const download = deferred<Response>(), controller = new AbortController()
    mockFetch(() => download.promise)
    const pending = downloadAuthed('/file', 'file', { signal: controller.signal }).catch(error => error)
    controller.abort(); download.resolve(json({ message: 'old download expired' }, 401))
    expect((await pending).name).toBe('AbortError'); expect(getToken()).toBe('A')
    const old = deferred<Response>(); let current = true
    mockFetch(() => old.promise)
    const request = api('/private', { current: () => current }).catch(error => error)
    current = false; old.resolve(json({ message: 'superseded private request' }, 401))
    expect(await request).toMatchObject({ name: 'AbortError' }); expect(getToken()).toBe('A')
  })
  it.each([
    ['login', 'close'], ['login', 'mode'], ['login', 'route'],
    ['register', 'close'], ['register', 'mode'], ['register', 'route'],
  ] as const)('a delayed %s cannot commit credentials after its modal owner changes by %s', async (mode, cancel) => {
    setToken('A'); const pending = deferred<Response>()
    const { calls } = mockFetch(({ path }) => path === `/auth/${mode}` ? pending.promise : undefined)
    const result = renderPage(<ModalHarness />)
    await screen.findByText('Buyer')
    const ui = userEvent.setup()
    if (mode === 'register') {
      await ui.click(screen.getByRole('button', { name: '还没有账号？立即注册' }))
      await ui.type(screen.getByLabelText('昵称'), 'New user')
    }
    await ui.type(screen.getByLabelText('账号'), 'new-user')
    await ui.type(screen.getByLabelText('密码'), 'New12345')
    await ui.click(screen.getByRole('button', { name: mode === 'login' ? '登录' : '注册并进入' }))
    const sent = calls.find(call => call.path === `/auth/${mode}`)!
    expect(sent).toBeDefined()
    if (cancel === 'close') await ui.click(screen.getByRole('button', { name: '关闭对话框' }))
    else if (cancel === 'mode') await ui.click(screen.getByRole('button', { name: mode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录' }))
    else await result.navigate('/another')
    expect(sent.options.signal?.aborted).toBe(true)
    await settle(() => pending.resolve(json({ token: 'Late rejected session', user: other })))
    expect(getToken()).toBe('A')
    if (cancel !== 'route') expect(screen.getByTestId('identity')).toHaveTextContent('Buyer')
    expect(toast.success).not.toHaveBeenCalled()
  })
  it('later login wins even when fetch ignores abort, and logout invalidates in-flight login', async () => {
    const logins: ReturnType<typeof deferred<Response>>[] = []
    mockFetch(({ path }) => {
      if (path === '/auth/login') { const pending = deferred<Response>(); logins.push(pending); return pending.promise }
      if (path === '/auth/logout') return json({ ok: true })
    })
    renderPage(<AuthControls />)
    const switchButton = screen.getByRole('button', { name: 'Switch session' })
    fireEvent.click(switchButton); fireEvent.click(switchButton)
    await settle(() => logins[1].resolve(json({ token: 'B', user: other })))
    await settle(() => logins[0].resolve(json({ token: 'A', user })))
    expect(getToken()).toBe('B'); expect(screen.getByTestId('identity')).toHaveTextContent('Other')
    fireEvent.click(switchButton); fireEvent.click(screen.getByRole('button', { name: 'Logout session' }))
    await settle(() => logins[2].resolve(json({ token: 'C', user })))
    expect(getToken()).toBeNull(); expect(screen.getByTestId('identity')).toHaveTextContent('Anonymous')
  })
  it('the actual login form accepts legacy six/seven character passwords and labels its dialog', async () => {
    const close = vi.fn(), { calls } = mockFetch(({ path }) => path === '/auth/login' ? json({ token: 'A', user }) : undefined)
    renderPage(<AuthModal open onClose={close} />)
    const dialog = screen.getByRole('dialog', { name: '登录知核' }); expect(dialog).toHaveAttribute('aria-modal', 'true')
    const ui = userEvent.setup()
    await ui.type(screen.getByLabelText('账号'), 'legacy'); await ui.type(screen.getByLabelText('密码'), 'Old123')
    expect((screen.getByLabelText('密码') as HTMLInputElement).checkValidity()).toBe(true)
    await ui.click(screen.getByRole('button', { name: '登录' }))
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
    expect(JSON.parse(String(calls.find(x => x.path === '/auth/login')?.options.body)).password).toBe('Old123')
  })
  it('registration requires eight characters without imposing that limit on login', async () => {
    mockFetch(() => undefined); renderPage(<AuthModal open onClose={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: '还没有账号？立即注册' }))
    const password = screen.getByLabelText('密码') as HTMLInputElement
    expect(password.minLength).toBe(8)
    await userEvent.type(screen.getByLabelText('昵称'), 'Name'); await userEvent.type(screen.getByLabelText('账号'), 'account'); await userEvent.type(password, 'Old123')
    await userEvent.click(screen.getByRole('button', { name: '注册并进入' }))
    expect(fetch).not.toHaveBeenCalled()
  })
  it('registration supersedes an earlier login and is safe against its delayed failure', async () => {
    const pending = deferred<Response>()
    mockFetch(({ path }) => path === '/auth/login' ? pending.promise : path === '/auth/register' ? json({ token: 'Registered', user: other }) : undefined)
    renderPage(<AuthControls />)
    fireEvent.click(screen.getByRole('button', { name: 'Switch session' })); fireEvent.click(screen.getByRole('button', { name: 'Register session' }))
    await screen.findByText('Other'); await settle(() => pending.resolve(json({ message: 'old bad credentials' }, 401)))
    expect(getToken()).toBe('Registered'); expect(screen.getByTestId('identity')).toHaveTextContent('Other')
  })
  it('does not commit restoration after the provider unmounts', async () => {
    setToken('A'); const pending = deferred<Response>(); mockFetch(() => pending.promise)
    const result = renderPage(<AuthControls />); result.unmount()
    await act(async () => pending.resolve(json(user)))
    expect(getToken()).toBe('A')
  })
})
