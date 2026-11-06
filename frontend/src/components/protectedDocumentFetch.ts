import { getSessionGeneration, getToken, sessionMatches, setToken, superseded } from '../shared/api'

// Only the lesson's own download route can be used for authenticated document requests.
// In particular, neither a caller-supplied URL nor its query string is forwarded to fetch.
export function lessonRawPath(rawFileUrl?: string | null): string | null {
  if (!rawFileUrl) return null
  try {
    const parsed = new URL(rawFileUrl, window.location.origin)
    if (parsed.origin !== window.location.origin || !/^\/api\/lesson-raw\/[1-9]\d*$/.test(parsed.pathname)) return null
    return parsed.pathname
  } catch {
    return null
  }
}

export function lessonPreviewPath(rawFileUrl?: string | null): string | null {
  const path = lessonRawPath(rawFileUrl)
  return path?.replace('/lesson-raw/', '/lesson-preview/') ?? null
}

async function fetchLessonResource(path: string, signal: AbortSignal): Promise<Response> {
  // Paths are derived exclusively from lessonRawPath (including for previews).
  const token = getToken(), generation = getSessionGeneration()
  const response = await fetch(path, {
    signal,
    credentials: 'same-origin',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (signal.aborted || !sessionMatches(generation, token)) throw superseded()
  if (!response.ok) {
    if (response.status === 401 && token && sessionMatches(generation, token)) {
      // Match the rest of the app: an expired session cannot retain private previews.
      if (!signal.aborted && sessionMatches(generation, token)) setToken()
    }
    throw new Error(response.status === 403 ? '无权访问此文档' : `文档读取失败（${response.status}）`)
  }
  return response
}

export async function fetchLessonBlob(rawFileUrl: string, signal: AbortSignal): Promise<Blob> {
  const path = lessonRawPath(rawFileUrl)
  if (!path) throw new Error('原始文档地址无效')
  const generation = getSessionGeneration(), token = getToken()
  const response = await fetchLessonResource(path, signal)
  const blob = await response.blob()
  if (signal.aborted || !sessionMatches(generation, token)) throw superseded()
  return blob
}

export async function fetchLessonPreview(rawFileUrl: string, signal: AbortSignal): Promise<unknown> {
  const path = lessonPreviewPath(rawFileUrl)
  if (!path) throw new Error('演示文稿地址无效')
  const generation = getSessionGeneration(), token = getToken()
  const response = await fetchLessonResource(path, signal)
  const data: unknown = await response.json()
  if (signal.aborted || !sessionMatches(generation, token)) throw superseded()
  return data
}
