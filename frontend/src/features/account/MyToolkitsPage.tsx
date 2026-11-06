import { useEffect, useRef, useState } from 'react'
import { Layers, PackageOpen, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Empty } from '../../components/Empty'
import { api } from '../../shared/api'
import { money } from '../../shared/lib'
import { useScopedData } from '../../useRequestScope'
import type { Toolkit } from '../../types'
import { LoadState } from './LoadState'

export function MyToolkitsPage() {
  const remote = useScopedData<Toolkit[]>('/me/toolkits'), items = remote.data ?? [], scope = remote.scope
  const pending = useRef(new Set<number>()), [busyIds, setBusyIds] = useState<number[]>([])
  useEffect(() => { pending.current.clear(); setBusyIds([]) }, [scope.key])
  const change = async (x: Toolkit, action: 'offline' | 'delete' | 'publish') => {
    if (pending.current.has(x.id)) return
    if (action === 'delete' && !window.confirm(`确定删除《${x.title}》？已有购买记录的内容将仅下架，并保留已购权益。`)) return
    pending.current.add(x.id); setBusyIds([...pending.current]); const request = scope.begin(`mutation-${x.id}`)
    try {
      if (action === 'delete') {
        const result = await api<{ message: string; mode: 'offline' | 'deleted' }>(`/toolkits/${x.id}`, { method: 'DELETE', signal: request.signal })
        if (!request.current()) return
        remote.setData(prev => result.mode === 'deleted' ? prev?.filter(i => i.id !== x.id) : prev?.map(i => i.id === x.id ? { ...i, status: 'offline' } : i))
        toast.success(result.message)
      } else {
        await api(`/toolkits/${x.id}/status`, { method: 'PUT', body: JSON.stringify({ status: action === 'publish' ? 'published' : 'offline' }), signal: request.signal })
        if (!request.current()) return
        remote.setData(prev => prev?.map(i => i.id === x.id ? { ...i, status: action === 'publish' ? 'published' : 'offline' } : i))
        toast.success(action === 'publish' ? '已发布上架' : '已下架')
      }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { pending.current.delete(x.id); setBusyIds([...pending.current]) } }
  }
  const statusText = (s: string) => s === 'published' ? ['已发布', 'text-emerald-600'] : s === 'offline' ? ['已下架', 'text-red-500'] : ['草稿', 'text-amber-600']
  return <div><div className="mb-5 flex items-end justify-between"><div><h1 className="text-2xl font-black text-ink">我的工具包</h1><p className="mt-1 text-sm text-slate-400">管理你分享的科研方法</p></div><div className="flex gap-2"><Link to="/publish/batch" className="btn-ghost"><Layers size={16} />批量上传</Link><Link to="/publish" className="btn-primary"><Plus size={16} />发布</Link></div></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="space-y-3">{items.map(x => { const [label, color] = statusText(x.status); return <div key={x.id} className="panel flex items-center gap-4 p-4"><Link to={`/toolkits/${x.id}`} className="flex min-w-0 flex-1 items-center gap-4"><img src={x.cover} alt="" className="size-20 rounded-2xl object-cover" /><div className="min-w-0 flex-1"><p className="truncate font-black text-ink">{x.title}</p><div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400"><span>{money(x.price)}</span><span>{x.downloads} 次下载</span><span className={color}>{label}</span></div></div></Link><div className="flex shrink-0 flex-col gap-2 sm:flex-row">{x.status === 'draft' && <button disabled={busyIds.includes(x.id)} onClick={() => void change(x, 'publish')} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700">发布</button>}{x.status === 'published' && <button disabled={busyIds.includes(x.id)} onClick={() => void change(x, 'offline')} className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-200">下架</button>}<button disabled={busyIds.includes(x.id)} onClick={() => void change(x, 'delete')} className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-600">删除</button></div></div>})}</div> : <Empty icon={PackageOpen} title="还没有发布工具包" detail="把验证过的流程整理成教程，帮助更多同行。" action={<Link className="btn-primary" to="/publish">开始发布</Link>} />}</div>
}
