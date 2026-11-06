import { ClipboardList } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { Empty } from '../../components/Empty'
import { useScopedData } from '../../useRequestScope'
import { date, money } from '../../shared/lib'
import type { Order } from '../../types'
import { LoadState } from './LoadState'

export function OrdersPage() {
  const navigate = useNavigate(), remote = useScopedData<Order[]>('/me/orders'), items = remote.data ?? []
  const status = { pending: ['待支付', 'bg-amber-100 text-amber-700'], paid: ['已完成', 'bg-emerald-100 text-emerald-700'], cancelled: ['已取消', 'bg-slate-100 text-slate-500'], refunded: ['已退款', 'bg-slate-100 text-slate-500'] } as const
  return <div><div className="mb-5"><h1 className="text-2xl font-black text-ink">我的订单</h1><p className="mt-1 text-sm text-slate-400">查看购买记录和订单状态</p></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="space-y-4">{items.map(x => <div key={x.id} className="panel p-5"><div className="flex items-center justify-between border-b pb-3 text-xs text-slate-400"><span>{x.orderNo}</span><span className={`chip ${status[x.status][1]}`}>{status[x.status][0]}</span></div><div className="mt-4 flex gap-4"><img src={x.toolkit.cover} className="size-20 rounded-xl object-cover" /><div className="min-w-0 flex-1"><Link to={`/toolkits/${x.toolkit.id}`} className="line-clamp-1 font-bold text-ink">{x.toolkit.title}</Link><p className="mt-2 text-xs text-slate-400">{date(x.createdAt)}</p><p className="mt-2 text-sm font-black">{money(x.amount)}</p></div>{x.status === 'pending' && <button onClick={() => navigate(`/checkout/${x.id}`, { state: { order: x } })} className="btn-mint self-end">去支付</button>}{x.status === 'paid' && <Link to={`/toolkits/${x.toolkit.id}/learn`} className="btn-ghost self-end">去学习</Link>}</div></div>)}</div> : <Empty icon={ClipboardList} title="暂无订单" detail="购买付费工具包后，订单会出现在这里。" action={<Link className="btn-primary" to="/toolkits">看看工具包</Link>} />}</div>
}
