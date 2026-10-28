import { useEffect, useRef, useState } from 'react'
import { Filter, Search, SlidersHorizontal } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { api, cn } from '../lib'
import type { Toolkit } from '../types'
import { ToolkitCard } from '../components/ToolkitCard'
import { useAuth } from '../AuthContext'
import { useRequestScope } from '../useRequestScope'

const categories = ['全部', '生信分析', '单细胞', '空间组学', '结构生物学', '基因编辑']
type ListResult = { items: Toolkit[]; total: number; page: number; pages: number }
export function ToolkitListPage() {
  const [params, setParams] = useSearchParams(); const [items, setItems] = useState<Toolkit[]>([]); const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0); const [page, setPage] = useState(1), [pages, setPages] = useState(0), [remaining, setRemaining] = useState(0), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const { loading: authLoading } = useAuth(), queryKey = params.toString(), scope = useRequestScope(queryKey)
  const q = params.get('q') || '', category = params.get('category') || '全部', price = params.get('price') || 'all', sort = params.get('sort') || 'recommended'
  const [input, setInput] = useState(q)
  const timer = useRef<number | undefined>(undefined), inFlight = useRef(false)
  useEffect(() => { window.clearTimeout(timer.current); setInput(q); return () => window.clearTimeout(timer.current) }, [q, scope.key])
  const update = (key: string, value: string) => {
    window.clearTimeout(timer.current)
    setParams(prev => {
      const next = new URLSearchParams(prev)
      value && value !== 'all' && value !== '全部' ? next.set(key, value) : next.delete(key)
      next.delete('page'); return next
    })
  }
  const query = (nextPage: number) => { const next = new URLSearchParams(queryKey); next.set('page', String(nextPage)); next.set('per_page', '12'); return next.toString() }
  useEffect(() => {
    setLoading(true); setItems([]); setTotal(0); setPage(1); setPages(0); setRemaining(0); setError(''); inFlight.current = false
    if (authLoading) return
    const request = scope.begin(), initialPage = Math.max(1, Number(params.get('page')) || 1)
    inFlight.current = true
    void api<ListResult>(`/toolkits?${query(initialPage)}`, { signal: request.signal }).then(x => {
      if (request.current()) {
        setItems(x.items); setTotal(x.total); setPage(x.page); setPages(x.pages ?? Math.ceil(x.total / 12))
        setRemaining(Math.max(0, x.total - (x.page - 1) * 12 - x.items.length))
      }
    }).catch(e => { if (request.current()) setError(e.message) }).finally(() => { if (request.current()) { setLoading(false); inFlight.current = false } })
  }, [scope, authLoading, retry])
  const more = () => {
    if (inFlight.current || loading || page >= pages || remaining <= 0) return
    inFlight.current = true; setLoading(true); setError('')
    const request = scope.begin(), nextPage = page + 1
    void api<ListResult>(`/toolkits?${query(nextPage)}`, { signal: request.signal }).then(x => {
      if (!request.current()) return
      setItems(prev => [...prev, ...x.items.filter(item => !prev.some(old => old.id === item.id))]); setTotal(x.total); setPage(x.page); setPages(x.pages ?? Math.ceil(x.total / 12))
      setRemaining(Math.max(0, x.total - (x.page - 1) * 12 - x.items.length))
    }).catch(e => { if (request.current()) setError(e.message) }).finally(() => { if (request.current()) { setLoading(false); inFlight.current = false } })
  }
  return <div className="mx-auto max-w-[1440px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
    <div className="rounded-[2rem] bg-ink px-6 py-9 text-white sm:px-10"><p className="text-sm font-bold text-mint">工具知识库</p><h1 className="mt-2 text-3xl font-black sm:text-4xl">找到适合你的科研工具</h1><div className="mt-6 flex max-w-2xl items-center rounded-2xl bg-white p-2"><Search className="ml-2 text-slate-400" size={19} /><input aria-label="搜索工具包" value={input} onChange={e => { const v = e.target.value, request = scope.begin('search'); setInput(v); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => { if (request.current()) update('q', v) }, 400) }} placeholder="搜索工具、技术或关键词" className="min-w-0 flex-1 px-3 py-2 text-sm text-slate-900 outline-none" /></div></div>
    <div className="mt-7 flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between"><div className="flex gap-2 overflow-x-auto pb-1">{categories.map(x => <button key={x} onClick={() => update('category', x)} className={cn('shrink-0 rounded-full px-4 py-2 text-sm font-semibold', category === x ? 'bg-ink text-white' : 'bg-white text-slate-500 hover:text-ink')}>{x}</button>)}</div><div className="flex items-center gap-2"><span className="hidden items-center gap-2 text-sm text-slate-400 sm:flex"><SlidersHorizontal size={15} />筛选</span><select aria-label="价格筛选" value={price} onChange={e => update('price', e.target.value)} className="input py-2"><option value="all">全部价格</option><option value="free">免费</option><option value="paid">付费</option></select><select aria-label="排序" value={sort} onChange={e => update('sort', e.target.value)} className="input py-2"><option value="recommended">综合推荐</option><option value="popular">最多下载</option><option value="newest">最新发布</option><option value="price">价格从低到高</option></select></div></div>
    <div className="mt-7 flex items-center justify-between"><p className="text-sm text-slate-500"><b className="text-ink">{total}</b> 个工具包</p>{q && <p className="text-sm text-slate-400">“{q}” 的搜索结果</p>}</div>
    {error && <div role="alert" className="mt-5 text-red-600">{error}<button className="btn-ghost ml-3" onClick={() => items.length ? more() : setRetry(x => x + 1)}>重试</button></div>}
    {loading && !items.length ? <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/5] animate-pulse rounded-3xl bg-slate-200" />)}</div> : items.length ? <><div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{items.map(x => <ToolkitCard key={x.id} item={x} />)}</div>{page < pages && remaining > 0 && <div className="mt-8 text-center"><button disabled={loading} onClick={more} className="btn-ghost px-8 py-3">{loading ? '加载中…' : `加载更多（还有 ${remaining} 个）`}</button></div>}</> :<div className="mt-10 rounded-3xl border border-dashed bg-white py-20 text-center"><Filter className="mx-auto text-slate-300" /><h3 className="mt-4 font-bold">没有找到匹配的工具包</h3><button onClick={() => { window.clearTimeout(timer.current); setParams({}) }} className="mt-3 text-sm font-semibold text-emerald-700">清除筛选</button></div>}
  </div>
}
