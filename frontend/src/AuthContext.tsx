import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken } from './lib'
import type { User } from './types'

type AuthValue = {
  user: User | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, nickname: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = async () => {
    if (!getToken()) { setUser(null); setLoading(false); return }
    try { setUser(await api<User>('/auth/me')) }
    catch { setToken(); setUser(null) }
    finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [])

  const login = async (username: string, password: string) => {
    const result = await api<{ token: string; user: User }>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
    setToken(result.token); setUser(result.user)
  }
  const register = async (username: string, password: string, nickname: string) => {
    const result = await api<{ token: string; user: User }>('/auth/register', { method: 'POST', body: JSON.stringify({ username, password, nickname }) })
    setToken(result.token); setUser(result.user)
  }
  const logout = () => { setToken(); setUser(null) }
  return <AuthContext.Provider value={{ user, loading, login, register, logout, refresh }}>{children}</AuthContext.Provider>
}

export const useAuth = () => {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

