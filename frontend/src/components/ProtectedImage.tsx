import { useEffect, useState, type ImgHTMLAttributes } from 'react'
import { useAuth } from '../AuthContext'
import { getSessionGeneration, getToken, sessionMatches, setToken } from '../lib'
import { useRequestScope } from '../useRequestScope'

export function ProtectedImage({ src, alt = '', ...props }: ImgHTMLAttributes<HTMLImageElement>) {
  const { sessionVersion } = useAuth()
  const scope = useRequestScope(src || '')
  const [blob, setBlob] = useState<{ key: string; url: string }>()
  let protectedPath: string | undefined
  try {
    if (src) {
      const url = new URL(src, window.location.origin)
      if (url.origin === window.location.origin && /^\/api\/lesson-asset\/[^/]+$/.test(url.pathname)) protectedPath = url.pathname
    }
  } catch { /* Invalid URLs never receive credentials. */ }
  useEffect(() => {
    setBlob(undefined)
    if (!protectedPath) return
    const request = scope.begin(), token = getToken(), generation = getSessionGeneration()
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
  }, [protectedPath, scope, sessionVersion])
  if (!src) return null
  if (protectedPath && blob?.key !== scope.key) return <span role="status" className="text-xs text-slate-400">配图加载中或暂不可用</span>
  return <img {...props} alt={alt} src={protectedPath ? blob?.url : src} />
}
