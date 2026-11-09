import { useEffect, useState, type ImgHTMLAttributes } from 'react'
import { useAuth } from '../AuthContext'
import { getSessionGeneration, getToken, sessionMatches, setToken } from '../shared/api'
import { useRequestScope } from '../useRequestScope'

export function ProtectedImage({ src, alt = '', protectedCover = false, ...props }: ImgHTMLAttributes<HTMLImageElement> & { protectedCover?: boolean }) {
  const { sessionVersion } = useAuth()
  const scope = useRequestScope(src || '')
  const [blob, setBlob] = useState<{ key: string; url: string }>()
  let protectedPath: string | undefined
  try {
    if (src) {
      const url = new URL(src, window.location.origin)
      if (url.origin === window.location.origin && (/^\/api\/lesson-asset\/[^/]+$/.test(url.pathname) ||
        (protectedCover && /^\/api\/uploads\/[^/]+$/.test(url.pathname)))) protectedPath = url.pathname
    }
  } catch { /* Invalid URLs never receive credentials. */ }
  useEffect(() => {
    setBlob(undefined)
    if (!protectedPath) return
    const token = getToken()
    // A private draft cover has no anonymous preview; do not refetch it after logout.
    if (protectedCover && !token) return
    const request = scope.begin(), generation = getSessionGeneration()
    let objectURL: string | undefined
    void fetch(protectedPath, { signal: request.signal, headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(async response => {
        if (!response.ok) {
          if (response.status === 401 && token && sessionMatches(generation, token)) {
            if (request.current()) setToken()
          }
          throw new Error('配图暂不可用')
        }
        return response.blob()
      }).then(value => {
        if (!request.current()) return
        objectURL = URL.createObjectURL(value); setBlob({ key: scope.key, url: objectURL })
      }).catch(() => {})
    return () => { if (objectURL) URL.revokeObjectURL(objectURL) }
  }, [protectedPath, protectedCover, scope, sessionVersion])
  if (!src) return null
  if (protectedPath && blob?.key !== scope.key) return <span role="status" className="text-xs text-slate-400">配图加载中或暂不可用</span>
  return <img {...props} alt={alt} src={protectedPath ? blob?.url : src} />
}
