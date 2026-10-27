import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Banknote, Box, ClipboardCheck, ShieldCheck, Star, Users } from 'lucide-react'
import { Link, Navigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, cn, money } from '../lib'
import type { Order, Toolkit } from '../types'

const statusLabel: Record<string, [string, string]> = {
  published: ['已上架', 'bg-emerald-50 text-emerald-700'],
  draft: ['草稿', 'bg-slate-100 text-slate-500'],
  offline: ['已下架', 'bg-red-50 text-red-600'],
}
const orderStatusLabel: Record<string, string> = { pending: '待支付', paid: '已支付', cancelled: '已取消', refunded: '已退款' }

export function AdminPage() {
  const { user } = useAuth()
  const [stats, setStats] = useState<{ users: number; toolkits: number; orders: number; revenue: number }>()
  const [items, setItems] = useState<Toolkit[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const load = useCallback(() => {
    Promise.all([
      api<typeof stats>('/admin/stats'),
      api<Toolkit[]>('/admin/toolkits'),
      api<Order[]>('/admin/orders'),
    ]).then(([s, t, o]) => { setStats(s); setItems(t); setOrders(o) }).catch(e => toast.error(e.message))
  }, [])
  useEffect(() => { if (user?.role === 'admin') load() }, [user, load])
  if (!user) return <Navigate to="/" />
  if (user.role !== 'admin') return <Navigate to="/me" />
  const cards = [{ label: '注册用户', value: stats?.users || 0, icon: Users }, { label: '工具包', value: stats?.toolkits || 0, icon: Box }, { label: '已支付订单', value: stats?.orders || 0, icon: ClipboardCheck }, { label: '成交金额', value: money(stats?.revenue || 0), icon: Banknote }]
  const setStatus = async (item: Toolkit, status: 'published' | 'offline') => {
    try {
      const updated = await api<Toolkit>(`/toolkits/${item.id}/status`, { method: 'PUT', body: JSON.stringify({ status }) })
      setItems(prev => prev.map(x => x.id === item.id ? { ...x, status: updated.status } : x))
      toast.success(status === 'published' ? '已重新上架' : '已下架')
    } catch (e) { toast.error((e as Error).message) }
  }
  const toggleFeatured = async (item: Toolkit) => {
    try {
      const updated = await api<Toolkit>(`/admin/toolkits/${item.id}/featured`, { method: 'POST', body: JSON.stringify({ featured: !item.featured }) })
      setItems(prev => prev.map(x => x.id === item.id ? { ...x, featured: updated.featured } : x))
      toast.success(updated.featured ? '已设为精选' : '已取消精选')
    } catch (e) { toast.error((e as Error).message) }
  }
  const refund = async (order: Order) => {
    if (!window.confirm(`确认退款订单 ${order.orderNo}？将向买家退回 ${money(order.amount)} 并收回卖家分成。`)) return
    try {
      const updated = await api<Order>(`/admin/orders/${order.id}/refund`, { method: 'POST' })
      setOrders(prev => prev.map(x => x.id === order.id ? updated : x))
      toast.success('退款完成，余额已回滚')
      load()
    } catch (e) { toast.error((e as Error).message) }
  }
  return <div className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12"><Link to="/me" className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回个人中心</Link><div className="mt-5 flex items-center gap-3"><span className="grid size-12 place-items-center rounded-2xl bg-ink text-mint"><ShieldCheck size={22} /></span><div><h1 className="text-3xl font-black text-ink">管理中心</h1><p className="text-sm text-slate-400">平台经营数据与内容治理</p></div></div><div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{cards.map(({ label, value, icon: Icon }) => <div key={label} className="panel p-5"><Icon size={20} className="text-emerald-700" /><p className="mt-5 text-xs text-slate-400">{label}</p><p className="mt-1 text-2xl font-black text-ink">{value}</p></div>)}</div>
    <section className="panel mt-7 overflow-hidden"><div className="border-b p-6"><h2 className="font-black text-ink">内容管理</h2><p className="mt-1 text-xs text-slate-400">平台全部工具包（含草稿与已下架），可上下架与设置精选</p></div><div className="divide-y">{items.map(x => <div key={x.id} className="flex flex-wrap items-center gap-4 p-4 sm:px-6"><img src={x.cover} alt="" className="size-14 rounded-xl object-cover" /><div className="min-w-0 flex-1"><Link to={`/toolkits/${x.id}`} className="truncate text-sm font-bold text-ink">{x.title}</Link><p className="mt-1 text-xs text-slate-400">{x.author.nickname} · {x.category} · {money(x.price)} · {x.lessonCount} 节</p></div><span className={cn('chip', (statusLabel[x.status] || ['未知', 'bg-slate-100 text-slate-500'])[1])}>{(statusLabel[x.status] || ['未知'])[0]}</span>{x.featured && <span className="chip bg-amber-50 text-amber-600"><Star size={11} className="mr-1 inline fill-amber-400" />精选</span>}<div className="flex gap-2">{x.status === 'published' ? <button onClick={() => setStatus(x, 'offline')} className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-600">下架</button> : <button onClick={() => setStatus(x, 'published')} className="rounded-lg bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700">上架</button>}<button onClick={() => toggleFeatured(x)} className={cn('rounded-lg px-3 py-2 text-xs font-bold', x.featured ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-600')}>{x.featured ? '取消精选' : '设精选'}</button></div></div>)}</div></section>
    <section className="panel mt-7 overflow-hidden"><div className="border-b p-6"><h2 className="font-black text-ink">交易订单</h2><p className="mt-1 text-xs text-slate-400">最近 100 笔订单，可对已支付订单执行退款（回滚双方余额并收回分成）</p></div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-400"><tr><th className="px-6 py-3 font-bold">订单号</th><th className="px-4 py-3 font-bold">买家 → 卖家</th><th className="px-4 py-3 font-bold">商品</th><th className="px-4 py-3 font-bold">金额</th><th className="px-4 py-3 font-bold">状态</th><th className="px-6 py-3 font-bold">操作</th></tr></thead><tbody className="divide-y">{orders.map(o => <tr key={o.id}><td className="whitespace-nowrap px-6 py-3 text-xs text-slate-500">{o.orderNo}</td><td className="whitespace-nowrap px-4 py-3 text-xs">{o.buyer || '—'} → {o.seller}</td><td className="max-w-[220px] truncate px-4 py-3">{o.toolkit.title}</td><td className="whitespace-nowrap px-4 py-3 font-bold">{money(o.amount)}</td><td className="whitespace-nowrap px-4 py-3 text-xs">{orderStatusLabel[o.status] || o.status}</td><td className="whitespace-nowrap px-6 py-3">{o.status === 'paid' ? <button onClick={() => refund(o)} className="rounded-lg bg-red-50 px-3 py-1.5 text-xs font-bold text-red-600">退款</button> : <span className="text-xs text-slate-300">—</span>}</td></tr>)}</tbody></table>{!orders.length && <p className="py-10 text-center text-sm text-slate-400">暂无订单</p>}</div></section></div>
}
