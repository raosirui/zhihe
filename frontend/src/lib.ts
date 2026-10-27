import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...values: ClassValue[]) => twMerge(clsx(values))
export const money = (value: number) => value === 0 ? '免费' : `¥${value % 1 === 0 ? String(value) : value.toFixed(2)}`
export const date = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

const TOKEN = 'zhihe_token'
export const getToken = () => localStorage.getItem(TOKEN)
export const setToken = (value?: string) => value ? localStorage.setItem(TOKEN, value) : localStorage.removeItem(TOKEN)

export async function downloadAuthed(path: string, filename: string) {
  const token = getToken()
  const response = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!response.ok) throw new Error('下载失败，请稍后再试')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename
  document.body.appendChild(anchor); anchor.click(); anchor.remove()
  URL.revokeObjectURL(url)
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`/api${path}`, { ...options, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && token) { setToken(); window.dispatchEvent(new Event('zhihe:expired')) }
    throw new Error(data.message || '请求失败，请稍后再试')
  }
  return data as T
}
