import { useState } from 'react'
import { Star } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../../shared/api'
import { cn, money } from '../../shared/lib'
import { useRequestScope } from '../../useRequestScope'
import { AdminShell } from './AdminShell'
import { AdminToolbar } from './AdminToolbar'
import { Pagination } from '../../components/Pagination'
import { useAdminPaged } from './useAdminPaged'
import { PER_PAGE_DEFAULT } from './constants'
import type { Toolkit } from '../../types'

const statusLabel: Record<string, [string, string]> = {
  published: ['已上架', 'bg-emerald-50 text-emerald-700'],
  draft: ['草稿', 'bg-slate-100 text-slate-500'],
  offline: ['已下架', 'bg-red-50 text-red-600'],
}

export function AdminToolkitsPage() {
  const scope = useRequestScope('admin:toolkits')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(PER_PAGE_DEFAULT)
  const [busyIds, setBusyIds] = useState<number[]>([])
  const pending = new Set<number>()
  const query = new URLSearchParams({ page: String(page), per_page: String(perPage) })
  if (status) query.set('status', status)
  if (search.trim()) query.set('q', search.trim())
  const { data, loading, error, reload } = useAdminPaged<Toolkit>('/admin/toolkits', query.toString())
  const items = data?.items ?? []

  const reset = () => setPage(1)
  const change = async (item: Toolkit, next?: 'published' | 'offline') => {
    if (pending.has(item.id)) return
    pending.add(item.id); setBusyIds([...pending])
    const request = scope.begin(`mutation-${item.id}`)
    try {
      const updated = await api<Toolkit>(next ? `/toolkits/${item.id}/status` : `/admin/toolkits/${item.id}/featured`,
        { method: next ? 'PUT' : 'POST', body: JSON.stringify(next ? { status: next } : { featured: !item.featured }), signal: request.signal })
      if (!request.current()) return
      setBusyIds(current => { const next2 = new Set(current); next2.delete(item.id); return [...next2] })
      if (next) {
        toast.success(next === 'published' ? '已重新上架' : '已下架')
        // status change can drop a row out of the current filter; refetch cleanly
        reload()
      } else {
        toast.success(updated.featured ? '已设为精选' : '已取消精选')
      }
    } catch (e) {
      if (request.current()) toast.error((e as Error).message)
    } finally {
      if (request.current()) { pending.delete(item.id); setBusyIds(current => current.filter(x => x !== item.id)) }
    }
  }

  return <AdminShell title="工具包管理" subtitle="平台全部工具包（含草稿与已下架），分页检索并治理上架与精选">
    <div className="panel overflow-hidden">
      <AdminToolbar search={search} onSearch={value => { setSearch(value); reset() }}
        status={status} onStatus={value => { setStatus(value); reset() }}
        statuses={[['published', '已上架'], ['draft', '草稿'], ['offline', '已下架']]} placeholder="搜索标题 / 作者 / 别名" />
      {error && <p role="alert" className="p-4 text-sm text-red-600">{error}<button className="btn-ghost ml-3" onClick={reload}>重试</button></p>}
      <div className="divide-y">
        {loading && !items.length ? <p className="p-10 text-center text-sm text-slate-400">正在加载…</p> : !items.length ? <p className="p-10 text-center text-sm text-slate-400">没有匹配的工具包</p>
          : items.map(x => <div key={x.id} className="flex flex-wrap items-center gap-4 p-4 sm:px-6">
            <img src={x.cover} alt="" className="size-14 rounded-xl object-cover" />
            <div className="min-w-0 flex-1"><Link to={`/toolkits/${x.id}`} className="truncate text-sm font-bold text-ink hover:text-emerald-700">{x.title}</Link>
              <p className="mt-1 text-xs text-slate-400">#{x.id} · {x.author.nickname} · {x.category} · {money(x.price)} · {x.lessonCount} 节 · {new Date(x.createdAt + 'Z').toLocaleDateString('zh-CN')}</p></div>
            <span className={cn('chip', (statusLabel[x.status] || ['未知', 'bg-slate-100 text-slate-500'])[1])}>{(statusLabel[x.status] || ['未知'])[0]}</span>
            {x.featured && <span className="chip bg-amber-50 text-amber-600"><Star size={11} className="mr-1 inline fill-amber-400" />精选</span>}
            <div className="flex gap-2">
              {x.status === 'published'
                ? <button disabled={busyIds.includes(x.id)} onClick={() => void change(x, 'offline')} className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-600 disabled:opacity-40">下架</button>
                : <button disabled={busyIds.includes(x.id)} onClick={() => void change(x, 'published')} className="rounded-lg bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700 disabled:opacity-40">上架</button>}
              <button disabled={busyIds.includes(x.id)} onClick={() => void change(x)} className={cn('rounded-lg px-3 py-2 text-xs font-bold disabled:opacity-40', x.featured ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-600')}>{x.featured ? '取消精选' : '设精选'}</button>
            </div>
          </div>)}
      </div>
      <Pagination page={data?.page ?? page} pages={data?.pages ?? 0} total={data?.total ?? 0} perPage={perPage} disabled={loading}
        onPage={setPage} onPerPage={size => { setPerPage(size); reset() }} />
    </div>
  </AdminShell>
}
