import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...values: ClassValue[]) => twMerge(clsx(values))
export const money = (value: number) => value === 0 ? '免费' : `¥${value.toFixed(2).replace('.00', '')}`
export const date = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

const TOKEN = 'zhihe_token'
export const getToken = () => localStorage.getItem(TOKEN)
export const setToken = (value?: string) => value ? localStorage.setItem(TOKEN, value) : localStorage.removeItem(TOKEN)

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api${path}`, { ...options, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.message || '请求失败，请稍后再试')
  return data as T
}
