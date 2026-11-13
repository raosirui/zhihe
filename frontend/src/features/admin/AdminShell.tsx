import { ReactNode, useEffect, useState } from 'react'
import { Banknote, Boxes, LayoutDashboard, ShieldCheck, Users } from 'lucide-react'
import { Navigate, NavLink } from 'react-router-dom'
import { useAuth } from '../../AuthContext'
import { cn } from '../../shared/lib'

const tabs = [
  { to: '/admin', label: '概览', icon: LayoutDashboard, end: true },
  { to: '/admin/toolkits', label: '工具包管理', icon: Boxes },
  { to: '/admin/users', label: '用户管理', icon: Users },
  { to: '/admin/orders', label: '订单管理', icon: Banknote },
]

// Shared frame for every admin screen: guards the admin role, shows a sidebar
// of the four sections and a stable page header. `stats` renders the four
// headline tiles once on any admin page (the outline view passes them in).
export function AdminShell({ title, subtitle, children, stats }: {
  title: string; subtitle?: string; children: ReactNode
  stats?: { users: number; toolkits: number; orders: number; revenue: string }
}) {
  const { user, loading, restoreError, refresh } = useAuth()
  if (loading) return <div role="status" className="p-12 text-center">正在恢复管理员会话…</div>
  if (restoreError) return <div className="p-12 text-center"><p role="alert">会话恢复失败：{restoreError}</p><button className="btn-primary mt-4" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!user) return <Navigate to="/" replace />
  if (user.role !== 'admin') return <Navigate to="/me" replace />
  return <div className="mx-auto grid max-w-[1240px] gap-6 px-4 py-8 sm:px-6 lg:grid-cols-[210px_1fr] lg:py-12">
    <aside><nav aria-label="管理中心导航" className="sticky top-24 space-y-1.5">
      <div className="mb-3 flex items-center gap-3 px-1"><span className="grid size-10 place-items-center rounded-2xl bg-ink text-mint"><ShieldCheck size={19} /></span><div><p className="font-black text-ink">管理中心</p><p className="text-[11px] text-slate-400">平台治理</p></div></div>
      {tabs.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end}
        className={({ isActive }) => cn('flex items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-sm font-bold transition', isActive ? 'bg-ink text-white' : 'text-slate-500 hover:bg-white hover:text-ink')}><Icon size={17} />{label}</NavLink>)}
    </nav></aside>
    <main className="min-w-0">
      <div className="mb-6"><h1 className="text-2xl font-black text-ink sm:text-3xl">{title}</h1>{subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}</div>
      {stats && <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[
        { label: '注册用户', value: String(stats.users), icon: Users }, { label: '工具包', value: String(stats.toolkits), icon: Boxes },
        { label: '已支付订单', value: String(stats.orders), icon: Banknote }, { label: '成交金额', value: stats.revenue, icon: LayoutDashboard },
      ].map(({ label, value, icon: Icon }) => <div key={label} className="panel p-5"><Icon size={20} className="text-emerald-700" /><p className="mt-4 text-xs text-slate-400">{label}</p><p className="mt-1 text-2xl font-black text-ink">{value}</p></div>)}</div>}
      {children}
    </main>
  </div>
}
