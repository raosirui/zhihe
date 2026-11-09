const TOKEN = 'zhihe_token'
let sessionGeneration = 0
export const getToken = () => localStorage.getItem(TOKEN)
export const getSessionGeneration = () => sessionGeneration
export const setToken = (value?: string) => {
  value ? localStorage.setItem(TOKEN, value) : localStorage.removeItem(TOKEN)
  sessionGeneration++
  window.dispatchEvent(new Event('zhihe:session'))
}
export const sessionMatches = (generation: number, token: string | null) => generation === sessionGeneration && token === getToken()
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}
export const isAbort = (error: unknown) => error instanceof Error && error.name === 'AbortError'
export const superseded = () => new DOMException('请求已被新的操作取代', 'AbortError')

function expire(status: number, token: string | null, generation: number, credentials = false) {
  // Login/register 401 is a credential error, not an expired authenticated session.
  if (status === 401 && token && !credentials && sessionMatches(generation, token)) {
    setToken()
    window.dispatchEvent(new Event('zhihe:expired'))
  }
}
async function responseError(response: Response) {
  const data = await response.json().catch(() => ({}))
  return new ApiError(data.message || '请求失败，请稍后再试', response.status)
}

// Backend resource URLs already include /api; page calls generally omit it.
// Normalize only here so downloads and JSON/blob requests share one boundary.
function apiUrl(path: string) {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('API 路径无效')
  return path === '/api' || path.startsWith('/api/') ? path : `/api${path}`
}

type BlobOptions = { signal?: AbortSignal; current?: () => boolean }
export async function fetchAuthedBlob(path: string, options: BlobOptions = {}) {
  const token = getToken(), generation = getSessionGeneration()
  const response = await fetch(apiUrl(path), { signal: options.signal, headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (options.signal?.aborted || (options.current && !options.current()) || !sessionMatches(generation, token)) throw superseded()
  if (!response.ok) { expire(response.status, token, generation); throw await responseError(response) }
  const blob = await response.blob()
  if (!sessionMatches(generation, token) || options.signal?.aborted || (options.current && !options.current())) throw superseded()
  return { blob, contentType: response.headers.get('Content-Type')?.split(';', 1)[0].toLowerCase() || blob.type.split(';', 1)[0].toLowerCase() }
}

export async function downloadAuthed(path: string, filename: string, options: BlobOptions = {}) {
  const { blob } = await fetchAuthedBlob(path, options)
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = filename
    document.body.appendChild(anchor); anchor.click(); anchor.remove()
  } finally { URL.revokeObjectURL(url) }
}

export async function api<T>(path: string, options: RequestInit & { current?: () => boolean } = {}): Promise<T> {
  const credentials = path === '/auth/login' || path === '/auth/register'
  const token = getToken(), generation = getSessionGeneration()
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (token && !credentials) headers.set('Authorization', `Bearer ${token}`)
  if (credentials) headers.delete('Authorization')
  const { current, ...init } = options
  const response = await fetch(apiUrl(path), { ...init, headers })
  // An aborted/superseded request can still resolve in a fetch implementation.
  // It must not expire the unchanged old token while a newer login is pending.
  if (options.signal?.aborted || (current && !current())) throw superseded()
  if (!response.ok) { expire(response.status, token, generation, credentials); throw await responseError(response) }
  return await response.json() as T
}
