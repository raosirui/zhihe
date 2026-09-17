import { useState } from 'react'
import { Home, LibraryBig, UserRound, LogOut, WalletCards, ShieldCheck, Plus } from 'lucide-react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../AuthContext'
import { cn, money } from '../lib'
import { AuthModal } from './AuthModal'
import { Logo } from './Logo'

const nav = [{ to: '/', label: '首页', icon: Home }, { to: '/toolkits', label: '工具包', icon: LibraryBig }, { to: '/me', label: '我的', icon: UserRound }]

export function Layout() {
  const { user, logout, loading } = useAuth()
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
              <Link to="/me" className="grid size-10 place-items-center rounded-full bg-pine text-sm font-black text-mint ring-2 ring-white">{user.nickname.slice(0, 1)}</Link>
              <div className="invisible absolute right-0 top-12 w-52 translate-y-2 rounded-2xl border bg-white p-2 opacity-0 shadow-xl transition group-hover:visible group-hover:translate-y-0 group-hover:opacity-100">
                <div className="border-b p-3"><p className="font-bold">{user.nickname}</p><p className="mt-1 text-xs text-slate-400">{user.role === 'admin' ? '超级管理员' : `余额 ${money(user.balance)}`}</p></div>
                {user.role === 'admin' && <Link to="/admin" className="mt-1 flex items-center gap-2 rounded-xl p-2.5 text-sm hover:bg-slate-50"><ShieldCheck size={16} />管理中心</Link>}
                <button onClick={() => { logout(); navigate('/') }} className="flex w-full items-center gap-2 rounded-xl p-2.5 text-sm text-red-500 hover:bg-red-50"><LogOut size={16} />退出登录</button>
              </div>
            </div>
          </> : !loading && <button onClick={() => setAuthOpen(true)} className="btn-primary px-4 sm:px-5"><span className="sm:hidden">登录</span><span className="hidden sm:inline">登录 / 注册</span></button>}
        </div>
      </div>
    </header>
    <main className={cn('min-h-[calc(100vh-64px)] pb-24 md:pb-8', location.pathname === '/' && 'pb-0')}><Outlet context={{ openAuth: () => setAuthOpen(true) }} /></main>
    <div className="fixed bottom-3 left-3 right-3 z-50 grid grid-cols-3 rounded-2xl border border-white/70 bg-white/90 p-1.5 shadow-2xl backdrop-blur-xl md:hidden">
      {nav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => cn('flex flex-col items-center gap-1 rounded-xl py-2 text-[11px] font-semibold', isActive ? 'bg-ink text-mint' : 'text-slate-400')}><Icon size={19} />{label}</NavLink>)}
    </div>
    <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} />
  </div>
}
