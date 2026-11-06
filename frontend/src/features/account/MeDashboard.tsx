import { Bookmark, ChevronRight, ShoppingBag, WalletCards } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../AuthContext'
import { useScopedData } from '../../useRequestScope'
import { money } from '../../shared/lib'
import type { Order, Toolkit } from '../../types'
import { LoadState } from './LoadState'

export function MeDashboard() {
  const { user } = useAuth(), f = useScopedData<Toolkit[]>('/me/favorites'), o = useScopedData<Order[]>('/me/orders')
  const favorites = f.data ?? [], orders = o.data ?? []
  return <div className="space-y-6"><LoadState loading={f.loading || o.loading} error={f.error || o.error} retry={() => { f.reload(); o.reload() }} /><div className="grid gap-4 sm:grid-cols-3"><Link to="/me/wallet" className="panel p-5"><WalletCards className="text-emerald-700" /><p className="mt-4 text-xs text-slate-400">可用余额</p><p className="mt-1 text-2xl font-black text-ink">{money(user?.balance || 0)}</p></Link><Link to="/me/favorites" className="panel p-5"><Bookmark className="text-violet-600" /><p className="mt-4 text-xs text-slate-400">已收藏</p><p className="mt-1 text-2xl font-black text-ink">{favorites.length}<small className="ml-1 text-xs font-medium text-slate-400">个</small></p></Link><Link to="/me/orders" className="panel p-5"><ShoppingBag className="text-amber-600" /><p className="mt-4 text-xs text-slate-400">已购内容</p><p className="mt-1 text-2xl font-black text-ink">{orders.filter(x => x.status === 'paid').length}<small className="ml-1 text-xs font-medium text-slate-400">个</small></p></Link></div><div className="panel p-6"><div className="flex justify-between"><h2 className="font-black text-ink">最近收藏</h2><Link to="/me/favorites" className="text-xs font-bold text-emerald-700">查看全部</Link></div><div className="mt-4 space-y-3">{favorites.slice(0, 3).map(x => <Link key={x.id} to={`/toolkits/${x.id}`} className="flex items-center gap-3 rounded-2xl bg-slate-50 p-3"><img src={x.cover} className="size-14 rounded-xl object-cover" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold">{x.title}</p><p className="mt-1 text-xs text-slate-400">{x.category} · {money(x.price)}</p></div><ChevronRight size={16} className="text-slate-300" /></Link>)}{!favorites.length && <p className="py-8 text-center text-sm text-slate-400">还没有收藏内容</p>}</div></div></div>
}
