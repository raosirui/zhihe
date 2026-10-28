import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, getSessionGeneration, getToken, isAbort, sessionMatches, setToken, superseded } from './lib'
import type { User } from './types'

type AuthCaller = { signal?: AbortSignal; current?: () => boolean }

type AuthValue = {
  user: User | null
  loading: boolean
  restoreError: string | null
  sessionVersion: number
  login: (username: string, password: string, caller?: AuthCaller) => Promise<void>
  register: (username: string, password: string, nickname: string, caller?: AuthCaller) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
}
const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [sessionVersion, setSessionVersion] = useState(getSessionGeneration)
  const currentUser = useRef(user); currentUser.current = user
  const mounted = useRef(false), sequence = useRef(0), controller = useRef<AbortController | null>(null)
  const begin = useCallback(() => {
    controller.current?.abort()
    const abort = new AbortController(); controller.current = abort
    const request = ++sequence.current, generation = getSessionGeneration(), token = getToken()
    return { signal: abort.signal, cancel: () => abort.abort(), current: () => mounted.current && !abort.signal.aborted && request === sequence.current && sessionMatches(generation, token) }
  }, [])
  const refresh = useCallback(async () => {
    const request = begin()
    setRestoreError(null)
    if (!getToken()) { setUser(null); setLoading(false); return }
    setLoading(true)
    try {
      const restored = await api<User>('/auth/me', { signal: request.signal, current: request.current })
      if (!request.current()) throw superseded()
      setUser(restored)
    } catch (error) {
      if (request.current() && !isAbort(error)) setRestoreError((error as Error).message)
      throw error
    } finally { if (request.current()) setLoading(false) }
  }, [begin])
  useEffect(() => {
    mounted.current = true
    const session = () => {
      sequence.current++; controller.current?.abort()
      setSessionVersion(getSessionGeneration()); setUser(null); setLoading(false); setRestoreError(null)
    }
    const storage = (event: StorageEvent) => { if (event.key === 'zhihe_token' || event.key === null) { setToken(getToken() || undefined); void refresh().catch(() => {}) } }
    window.addEventListener('zhihe:session', session)
    window.addEventListener('storage', storage)
    void refresh().catch(() => {})
    return () => {
      mounted.current = false; sequence.current++; controller.current?.abort()
      window.removeEventListener('zhihe:session', session); window.removeEventListener('storage', storage)
    }
  }, [refresh])
  const authenticate = useCallback(async (path: string, body: object, caller: AuthCaller = {}) => {
    const callerCurrent = () => !caller.signal?.aborted && (caller.current?.() ?? true)
    if (!callerCurrent()) throw superseded()
    const request = begin(); setLoading(false)
    caller.signal?.addEventListener('abort', request.cancel, { once: true })
    const current = () => request.current() && callerCurrent()
    try {
      const result = await api<{ token: string; user: User }>(path, { method: 'POST', body: JSON.stringify(body), signal: request.signal, current })
      // The provider stays mounted when a caller's modal closes or route changes.
      // Validate both owners before committing credentials, even if fetch ignores abort.
      if (!current()) throw superseded()
      setToken(result.token); setUser(result.user); setLoading(false); setRestoreError(null)
    } catch (error) {
      if (current() && getToken() && !currentUser.current && !isAbort(error)) setRestoreError('登录未成功，原会话尚未恢复；可重试恢复原会话。')
      throw error
    } finally { caller.signal?.removeEventListener('abort', request.cancel) }
  }, [begin])
  const login = useCallback((username: string, password: string, caller?: AuthCaller) => authenticate('/auth/login', { username, password }, caller), [authenticate])
  const register = useCallback((username: string, password: string, nickname: string, caller?: AuthCaller) => authenticate('/auth/register', { username, password, nickname }, caller), [authenticate])
  const logout = useCallback(() => {
    // fetch captures the old token before synchronously invalidating local work.
    if (getToken()) void api('/auth/logout', { method: 'POST' }).catch(() => {})
    setToken(); setUser(null); setLoading(false); setRestoreError(null)
  }, [])
  return <AuthContext.Provider value={{ user, loading, restoreError, sessionVersion, login, register, logout, refresh }}>{children}</AuthContext.Provider>
}
export const useAuth = () => {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
