import { useEffect, useState } from 'react'
import { api } from '../../shared/api'
import { useRequestScope } from '../../useRequestScope'

export type Paged<T> = { items: T[]; total: number; page: number; pages: number }

// One hook drives every admin table: it refetches whenever the query string
// (page / per_page / filters) changes, guards against stale responses with the
// request scope, and never clears the current rows mid-refetch so the table
// doesn't flash empty while paging.
export function useAdminPaged<T>(path: string, query: string) {
  const scope = useRequestScope(`admin:${path}:${query}`)
  const [data, setData] = useState<Paged<T>>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const request = scope.begin()
    setLoading(true); setError('')
    void api<Paged<T>>(`${path}?${query}`, { signal: request.signal }).then(result => {
      if (request.current()) setData(result)
    }).catch(e => { if (request.current()) setError((e as Error).message) })
      .finally(() => { if (request.current()) setLoading(false) })
  }, [scope, path, query, retry])
  return { data, loading, error, reload: () => setRetry(x => x + 1) }
}
