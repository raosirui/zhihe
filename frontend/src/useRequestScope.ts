import { useCallback, useEffect, useMemo, useRef, useState, type SetStateAction } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'
import { api, getSessionGeneration, getToken, sessionMatches } from './lib'

export type ScopedRequest = { signal: AbortSignal; current: () => boolean }

// Cancellation saves work; correctness comes from the independent generation,
// token, route/page key and per-channel sequence checks (also after await).
export function useRequestScope(pageKey = '') {
  const { sessionVersion } = useAuth()
  const location = useLocation()
  const key = `${sessionVersion}:${location.key}:${location.pathname}:${pageKey}`
  const latest = useRef(key); latest.current = key
  const mounted = useRef(false)
  const channels = useRef(new Map<string, { sequence: number; controller: AbortController }>())
  const scope = useMemo(() => ({
    key,
    begin(channel = 'load'): ScopedRequest {
      const previous = channels.current.get(channel)
      previous?.controller.abort()
      const sequence = (previous?.sequence ?? 0) + 1, controller = new AbortController()
      channels.current.set(channel, { sequence, controller })
      const generation = getSessionGeneration(), token = getToken()
      return { signal: controller.signal, current: () => mounted.current && latest.current === key && !controller.signal.aborted && channels.current.get(channel)?.sequence === sequence && sessionMatches(generation, token) }
    },
  }), [key])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; channels.current.forEach(x => x.controller.abort()) }
  }, [key])
  return scope
}

export function useScopedData<T>(path: string, requiresUser = true) {
  const { user, loading: authLoading, restoreError } = useAuth()
  const scope = useRequestScope(path)
  const [stored, setStored] = useState<{ key: string; data: T }>(), [loading, setLoading] = useState(true), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const reload = useCallback(() => setRetry(x => x + 1), [])
  const setData = useCallback((value: SetStateAction<T | undefined>) => {
    setStored(prev => {
      const current = prev?.key === scope.key ? prev.data : undefined
      const data = typeof value === 'function' ? (value as (previous: T | undefined) => T | undefined)(current) : value
      return data === undefined ? undefined : { key: scope.key, data }
    })
  }, [scope.key])
  useEffect(() => {
    setStored(undefined); setLoading(true); setError('')
    if (authLoading) return
    if ((requiresUser && !user) || restoreError) { setLoading(false); return }
    const request = scope.begin()
    void api<T>(path, { signal: request.signal }).then(data => { if (request.current()) setStored({ key: scope.key, data }) })
      .catch(e => { if (request.current()) setError(e.message) }).finally(() => { if (request.current()) setLoading(false) })
  }, [scope, path, authLoading, user?.id, requiresUser, restoreError, retry])
  return { data: stored?.key === scope.key ? stored.data : undefined, setData, loading, error, reload, scope }
}
