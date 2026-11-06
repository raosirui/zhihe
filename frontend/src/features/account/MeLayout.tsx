import { Box, ChevronRight, ClipboardList, Heart, WalletCards } from 'lucide-react'
import { NavLink, Outlet, useOutletContext } from 'react-router-dom'
import { useAuth } from '../../AuthContext'
import { Empty } from '../../components/Empty'
import { cn } from '../../shared/lib'

const meNav = [{ to: '/me/wallet', label: '钱包', icon: WalletCards }, { to: '/me/favorites', label: '我的收藏', icon: Heart }, { to: '/me/toolkits', label: '我的工具包', icon: Box }, { to: '/me/orders', label: '订单', icon: ClipboardList }]

export function MeLayout() {
  const { user, loading, restoreError, refresh } = useAuth(), context = useOutletContext<{ openAuth: () => void }>()
  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <Empty title="会话恢复失败" detail={restoreError} action={<button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button>} />
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><WalletCards size={42} className="mx-auto text-slate-300" /><h1 className="mt-4 text-xl font-black">请先登录查看个人中心</h1><button onClick={context.openAuth} className="btn-primary mt-6">登录 / 注册</button></div>
  return <div className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12"><div className="relative overflow-hidden rounded-3xl bg-ink p-6 text-white sm:p-8"><div className="absolute right-10 top-0 size-48 rounded-full bg-mint/10 blur-3xl" /><div className="relative flex items-center gap-4"><span className="grid size-16 place-items-center rounded-2xl bg-mint text-2xl font-black text-ink">{user.nickname.slice(0, 1)}</span><div><div className="flex items-center gap-2"><h1 className="text-2xl font-black">{user.nickname}</h1><span className="chip bg-white/10 text-[10px] text-mint">{user.role === 'admin' ? '超级管理员' : '研究者'}</span></div><p className="mt-1 text-sm text-white/45">{user.bio}</p></div></div></div><div className="mt-6 grid gap-7 lg:grid-cols-[230px_1fr]"><aside><nav className="panel flex gap-2 overflow-x-auto p-2 lg:block lg:space-y-1">{meNav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => cn('flex shrink-0 items-center gap-3 rounded-2xl px-4 py-3 text-sm font-semibold transition', isActive ? 'bg-ink text-white' : 'text-slate-500 hover:bg-slate-50 hover:text-ink')}><Icon size={17} />{label}<ChevronRight className="ml-auto hidden lg:block" size={15} /></NavLink>)}</nav></aside><main className="min-w-0"><Outlet context={context} /></main></div></div>
}
