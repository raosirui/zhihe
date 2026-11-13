import { useState } from 'react'
import { Banknote, Boxes, Heart, Home, LayoutDashboard, LibraryBig, LogOut, Plus, ShieldCheck, ShoppingBag, UserRound, Users, WalletCards } from 'lucide-react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../app/AuthProvider'
import { cn, money } from '../shared/lib'
import { AuthModal } from '../features/auth/AuthModal'
import { Logo } from './Logo'

const nav = [{ to: '/', label: '首页', icon: Home }, { to: '/toolkits', label: '工具包', icon: LibraryBig }, { to: '/me', label: '我的', icon: UserRound }]
const accountNav = [
  { to: '/me/wallet', label: '钱包', icon: WalletCards },
  { to: '/me/favorites', label: '我的收藏', icon: Heart },
  { to: '/me/toolkits', label: '我的工具包', icon: LibraryBig },
  { to: '/me/orders', label: '订单', icon: ShoppingBag },
]
const adminNav = [
  { to: '/admin', label: '管理概览', icon: LayoutDashboard },
  { to: '/admin/toolkits', label: '工具包管理', icon: Boxes },
  { to: '/admin/users', label: '用户管理', icon: Users },
  { to: '/admin/orders', label: '订单管理', icon: Banknote },
]

export function Layout() {
  const { user, logout, loading, restoreError, refresh } = useAuth()
  const [authOpen, setAuthOpen] = useState(false)
  const location = useLocation(); const navigate = useNavigate()
  return <div className="min-h-screen">
    <header className="sticky top-0 z-50 border-b border-slate-200/70 bg-paper/90 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <Logo />
        <nav className="hidden items-center gap-1 rounded-2xl bg-white p-1 shadow-sm md:flex">
          {nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => cn('flex items-center gap-2 rounded-xl px-5 py-2 text-sm font-semibold transition', isActive ? 'bg-ink text-white' : 'text-slate-500 hover:text-ink')}><Icon size={16} />{label}</NavLink>)}
        </nav>
        <div className="flex items-center gap-2">
          {!loading && user ? <>
            <Link to="/publish" className="btn-ghost hidden sm:flex"><Plus size={16} />发布工具包</Link>
            <button onClick={() => navigate('/me/wallet')} className="hidden rounded-xl bg-white px-3 py-2 text-sm font-bold text-ink shadow-sm lg:block"><WalletCards size={16} className="mr-1.5 inline text-emerald-600" />{money(user.balance)}</button>
            <div className="group relative">
              <Link to="/me" aria-label="个人中心" className="grid size-10 place-items-center rounded-full bg-pine text-sm font-black text-mint ring-2 ring-white">{user.nickname.slice(0, 1)}</Link>
              <div className="invisible absolute right-0 top-10 max-h-[70vh] w-56 translate-y-2 overflow-y-auto rounded-2xl border bg-white p-2 opacity-0 shadow-xl transition group-hover:visible group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100">
                <div className="border-b p-3"><p className="font-bold">{user.nickname}</p><p className="mt-1 text-xs text-slate-400">{user.role === 'admin' ? '超级管理员' : `余额 ${money(user.balance)}`}</p></div>
                <nav aria-label="个人中心快捷导航" className="border-b py-1">
                  {accountNav.map(({ to, label, icon: Icon }) => <Link key={to} to={to} className="flex items-center gap-2 rounded-xl p-2.5 text-sm text-slate-600 hover:bg-slate-50 hover:text-ink"><Icon size={16} />{label}</Link>)}
                </nav>
                {user.role === 'admin' && <nav aria-label="管理中心导航" className="border-b py-1">
                  <p className="flex items-center gap-1.5 px-2.5 pb-1 pt-2 text-[11px] font-black uppercase tracking-wider text-slate-400"><ShieldCheck size={13} />管理中心</p>
                  {adminNav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === '/admin'} className={({ isActive }) => cn('flex items-center gap-2 rounded-xl p-2.5 text-sm hover:bg-slate-50', isActive ? 'font-bold text-emerald-700' : 'text-slate-600 hover:text-ink')}><Icon size={16} />{label}</NavLink>)}
                </nav>}
                <button onClick={() => { logout(); navigate('/') }} className="mt-1 flex w-full items-center gap-2 rounded-xl border-t p-2.5 pt-3 text-sm text-red-500 hover:bg-red-50"><LogOut size={16} />退出登录</button>
              </div>
            </div>
          </> : !loading && <button onClick={() => setAuthOpen(true)} className="btn-primary px-4 sm:px-5"><span className="sm:hidden">登录</span><span className="hidden sm:inline">登录 / 注册</span></button>}
        </div>
      </div>
    </header>
    {restoreError && <div role="alert" className="border-b bg-amber-50 px-4 py-3 text-center text-sm text-amber-900">会话暂未恢复：{restoreError}<button className="btn-ghost ml-3" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>}
    <main className={cn('min-h-[calc(100vh-64px)] pb-24 md:pb-8', location.pathname === '/' && 'pb-0')}><Outlet context={{ openAuth: () => setAuthOpen(true) }} /></main>
    <div className="fixed bottom-3 left-3 right-3 z-50 grid grid-cols-3 rounded-2xl border border-white/70 bg-white/90 p-1.5 shadow-2xl backdrop-blur-xl md:hidden">
      {nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => cn('flex flex-col items-center gap-1 rounded-xl py-2 text-[11px] font-semibold', isActive ? 'bg-ink text-mint' : 'text-slate-400')}><Icon size={19} />{label}</NavLink>)}
    </div>
    <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} />
  </div>
}
