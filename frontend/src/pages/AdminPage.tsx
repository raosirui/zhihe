import { useEffect, useState } from 'react'
import { ArrowLeft, Banknote, Box, ClipboardCheck, ShieldCheck, Users } from 'lucide-react'
import { Link, Navigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, money } from '../lib'
import type { Toolkit } from '../types'

export function AdminPage() {
  const { user } = useAuth(); const [stats, setStats] = useState<{ users: number; toolkits: number; orders: number; revenue: number }>(); const [items, setItems] = useState<Toolkit[]>([])
  useEffect(() => { if (user?.role === 'admin') Promise.all([api<typeof stats>('/admin/stats'), api<{ items: Toolkit[] }>('/toolkits')]).then(([s, t]) => { setStats(s); setItems(t.items) }).catch(e => toast.error(e.message)) }, [user])
  if (!user) return <Navigate to="/" />
  if (user.role !== 'admin') return <Navigate to="/me" />
  const cards = [{ label: '注册用户', value: stats?.users || 0, icon: Users }, { label: '工具包', value: stats?.toolkits || 0, icon: Box }, { label: '已支付订单', value: stats?.orders || 0, icon: ClipboardCheck }, { label: '成交金额', value: money(stats?.revenue || 0), icon: Banknote }]
  const offline = async (item: Toolkit) => { try { await api(`/toolkits/${item.id}/status`, { method: 'PUT', body: JSON.stringify({ status: 'offline' }) }); setItems(items.filter(x => x.id !== item.id)); toast.success('工具包已下架') } catch (e) { toast.error((e as Error).message) } }
  return <div className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12"><Link to="/me" className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回个人中心</Link><div className="mt-5 flex items-center gap-3"><span className="grid size-12 place-items-center rounded-2xl bg-ink text-mint"><ShieldCheck size={22} /></span><div><h1 className="text-3xl font-black text-ink">管理中心</h1><p className="text-sm text-slate-400">平台经营数据与内容治理</p></div></div><div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{cards.map(({ label, value, icon: Icon }) => <div key={label} className="panel p-5"><Icon size={20} className="text-emerald-700" /><p className="mt-5 text-xs text-slate-400">{label}</p><p className="mt-1 text-2xl font-black text-ink">{value}</p></div>)}</div><section className="panel mt-7 overflow-hidden"><div className="border-b p-6"><h2 className="font-black text-ink">内容管理</h2><p className="mt-1 text-xs text-slate-400">查看已上架工具包，处理不合规内容</p></div><div className="divide-y">{items.map(x => <div key={x.id} className="flex items-center gap-4 p-4 sm:px-6"><img src={x.cover} className="size-14 rounded-xl object-cover" /><div className="min-w-0 flex-1"><Link to={`/toolkits/${x.id}`} className="truncate text-sm font-bold text-ink">{x.title}</Link><p className="mt-1 text-xs text-slate-400">{x.author.nickname} · {x.category} · {money(x.price)}</p></div><button onClick={() => offline(x)} className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-600">下架</button></div>)}</div></section></div>
}

