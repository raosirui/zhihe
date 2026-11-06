export function LoadState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (loading) return <p role="status" className="mb-4 text-sm text-slate-400">正在加载…</p>
  return error ? <div role="alert" className="mb-4 text-red-600">{error}<button className="btn-ghost ml-3" onClick={retry}>重试</button></div> : null
}
