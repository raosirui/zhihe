import { useEffect, useRef, useState } from 'react'
import { Filter, Search, SlidersHorizontal } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { api, cn } from '../lib'
import type { Toolkit } from '../types'
import { ToolkitCard } from '../components/ToolkitCard'

const categories = ['全部', '生信分析', '单细胞', '空间组学', '结构生物学', '基因编辑']
type ListResult = { items: Toolkit[]; total: number; page: number; pages: number }
export function ToolkitListPage() {
  const [params, setParams] = useSearchParams(); const [items, setItems] = useState<Toolkit[]>([]); const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0); const [page, setPage] = useState(1)
  const q = params.get('q') || '', category = params.get('category') || '全部', price = params.get('price') || 'all', sort = params.get('sort') || 'recommended'
  const [input, setInput] = useState(q)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => { setInput(q) }, [q])
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); value && value !== 'all' && value !== '全部' ? next.set(key, value) : next.delete(key); setParams(next) }
  useEffect(() => { setLoading(true); api<ListResult>(`/toolkits?${params.toString()}&page=1&per_page=12`).then(x => { setItems(x.items); setTotal(x.total); setPage(1) }).finally(() => setLoading(false)) }, [params])
  const more = () => { setLoading(true); api<ListResult>(`/toolkits?${params.toString()}&page=${page + 1}&per_page=12`).then(x => { setItems(prev => [...prev, ...x.items]); setPage(page + 1) }).finally(() => setLoading(false)) }
  return <div className="mx-auto max-w-[1440px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
    <div className="rounded-[2rem] bg-ink px-6 py-9 text-white sm:px-10"><p className="text-sm font-bold text-mint">工具知识库</p><h1 className="mt-2 text-3xl font-black sm:text-4xl">找到适合你的科研工具</h1><div className="mt-6 flex max-w-2xl items-center rounded-2xl bg-white p-2"><Search className="ml-2 text-slate-400" size={19} /><input value={input} onChange={e => { const v = e.target.value; setInput(v); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => update('q', v), 400) }} placeholder="搜索工具、技术或关键词" className="min-w-0 flex-1 px-3 py-2 text-sm text-slate-900 outline-none" /></div></div>
    <div className="mt-7 flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between"><div className="flex gap-2 overflow-x-auto pb-1">{categories.map(x => <button key={x} onClick={() => update('category', x)} className={cn('shrink-0 rounded-full px-4 py-2 text-sm font-semibold', category === x ? 'bg-ink text-white' : 'bg-white text-slate-500 hover:text-ink')}>{x}</button>)}</div><div className="flex items-center gap-2"><span className="hidden items-center gap-2 text-sm text-slate-400 sm:flex"><SlidersHorizontal size={15} />筛选</span><select value={price} onChange={e => update('price', e.target.value)} className="input py-2"><option value="all">全部价格</option><option value="free">免费</option><option value="paid">付费</option></select><select value={sort} onChange={e => update('sort', e.target.value)} className="input py-2"><option value="recommended">综合推荐</option><option value="popular">最多学习</option><option value="newest">最新发布</option><option value="price">价格从低到高</option></select></div></div>
    <div className="mt-7 flex items-center justify-between"><p className="text-sm text-slate-500"><b className="text-ink">{total}</b> 个工具包</p>{q && <p className="text-sm text-slate-400">“{q}” 的搜索结果</p>}</div>
    {loading && !items.length ? <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="aspect-[4/5] animate-pulse rounded-3xl bg-slate-200" />)}</div> : items.length ? <><div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{items.map(x => <ToolkitCard key={x.id} item={x} />)}</div>{items.length < total && <div className="mt-8 text-center"><button disabled={loading} onClick={more} className="btn-ghost px-8 py-3">{loading ? '加载中…' : `加载更多（还有 ${total - items.length} 个）`}</button></div>}</> :<div className="mt-10 rounded-3xl border border-dashed bg-white py-20 text-center"><Filter className="mx-auto text-slate-300" /><h3 className="mt-4 font-bold">没有找到匹配的工具包</h3><button onClick={() => setParams({})} className="mt-3 text-sm font-semibold text-emerald-700">清除筛选</button></div>}
  </div>
}
