import { ArrowRight, Banknote, Boxes, ShieldCheck, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useScopedData } from '../../useRequestScope'
import { money } from '../../shared/lib'
import { AdminShell } from './AdminShell'

const cards = [
  { to: '/admin/toolkits', title: '工具包管理', desc: '全量内容分页检索，一键上下架与设精选', icon: Boxes },
  { to: '/admin/users', title: '用户管理', desc: '按角色与关键词检索，查看发布、收入与消费', icon: Users },
  { to: '/admin/orders', title: '订单管理', desc: '交易流水分页查阅，支持状态与关键词筛选', icon: Banknote },
]

export function AdminPage() {
  const stats = useScopedData<{ users: number; toolkits: number; orders: number; revenue: number }>('/admin/stats', true)
  return <AdminShell title="管理中心" subtitle="平台经营数据与内容治理概览"
    stats={stats.data ? { users: stats.data.users, toolkits: stats.data.toolkits, orders: stats.data.orders, revenue: money(stats.data.revenue) } : undefined}>
    <p className="mb-3 text-sm font-bold text-ink">进入管理</p>
    <div className="grid gap-4 sm:grid-cols-3">{cards.map(({ to, title, desc, icon: Icon }) => <Link key={to} to={to}
      className="panel group flex flex-col p-6 transition hover:-translate-y-0.5 hover:shadow-lg">
      <span className="grid size-11 place-items-center rounded-xl bg-emerald-50 text-emerald-700"><Icon size={20} /></span>
      <p className="mt-4 flex items-center justify-between font-black text-ink">{title}<ArrowRight size={16} className="text-slate-300 transition group-hover:translate-x-1 group-hover:text-emerald-600" /></p>
      <p className="mt-1 text-xs leading-6 text-slate-400">{desc}</p>
    </Link>)}</div>
    <div className="panel mt-6 flex items-start gap-3 p-5 text-sm text-slate-500"><ShieldCheck size={18} className="mt-0.5 shrink-0 text-emerald-700" />
      <p>每个管理页均为独立分页视图；内容以最近创建排序，支持每页 10/20/50 条、页码直达与条件筛选。平台不支持退款，历史订单仅可查阅。</p></div>
  </AdminShell>
}
