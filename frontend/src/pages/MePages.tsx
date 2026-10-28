import { useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, BadgeDollarSign, Banknote, Bookmark, Box, ChevronRight, ClipboardList, Coins, CreditCard, Heart, History, Layers, PackageOpen, Plus, ShoppingBag, WalletCards } from 'lucide-react'
import { Link, NavLink, Outlet, useNavigate, useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, ApiError, cn, date, money } from '../lib'
import { useScopedData } from '../useRequestScope'
import type { Order, Toolkit, Transaction } from '../types'
import { ToolkitCard } from '../components/ToolkitCard'
import { Empty } from '../components/Empty'
import { Modal } from '../components/Modal'

function LoadState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (loading) return <p role="status" className="mb-4 text-sm text-slate-400">正在加载…</p>
  return error ? <div role="alert" className="mb-4 text-red-600">{error}<button className="btn-ghost ml-3" onClick={retry}>重试</button></div> : null
}

const meNav = [{ to: '/me/wallet', label: '钱包', icon: WalletCards }, { to: '/me/favorites', label: '我的收藏', icon: Heart }, { to: '/me/toolkits', label: '我的工具包', icon: Box }, { to: '/me/orders', label: '订单', icon: ClipboardList }]
export function MeLayout() {
  const { user, loading, restoreError, refresh } = useAuth(), context = useOutletContext<{ openAuth: () => void }>()
  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <Empty title="会话恢复失败" detail={restoreError} action={<button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button>} />
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><WalletCards size={42} className="mx-auto text-slate-300" /><h1 className="mt-4 text-xl font-black">请先登录查看个人中心</h1><button onClick={context.openAuth} className="btn-primary mt-6">登录 / 注册</button></div>
  return <div className="mx-auto max-w-[1240px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12"><div className="relative overflow-hidden rounded-3xl bg-ink p-6 text-white sm:p-8"><div className="absolute right-10 top-0 size-48 rounded-full bg-mint/10 blur-3xl" /><div className="relative flex items-center gap-4"><span className="grid size-16 place-items-center rounded-2xl bg-mint text-2xl font-black text-ink">{user.nickname.slice(0, 1)}</span><div><div className="flex items-center gap-2"><h1 className="text-2xl font-black">{user.nickname}</h1><span className="chip bg-white/10 text-[10px] text-mint">{user.role === 'admin' ? '超级管理员' : '研究者'}</span></div><p className="mt-1 text-sm text-white/45">{user.bio}</p></div></div></div><div className="mt-6 grid gap-7 lg:grid-cols-[230px_1fr]"><aside><nav className="panel flex gap-2 overflow-x-auto p-2 lg:block lg:space-y-1">{meNav.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => cn('flex shrink-0 items-center gap-3 rounded-2xl px-4 py-3 text-sm font-semibold transition', isActive ? 'bg-ink text-white' : 'text-slate-500 hover:bg-slate-50 hover:text-ink')}><Icon size={17} />{label}<ChevronRight className="ml-auto hidden lg:block" size={15} /></NavLink>)}</nav></aside><main className="min-w-0"><Outlet context={context} /></main></div></div>
}

export function MeDashboard() {
  const { user } = useAuth(), f = useScopedData<Toolkit[]>('/me/favorites'), o = useScopedData<Order[]>('/me/orders')
  const favorites = f.data ?? [], orders = o.data ?? []
  return <div className="space-y-6"><LoadState loading={f.loading || o.loading} error={f.error || o.error} retry={() => { f.reload(); o.reload() }} /><div className="grid gap-4 sm:grid-cols-3"><Link to="/me/wallet" className="panel p-5"><WalletCards className="text-emerald-700" /><p className="mt-4 text-xs text-slate-400">可用余额</p><p className="mt-1 text-2xl font-black text-ink">{money(user?.balance || 0)}</p></Link><Link to="/me/favorites" className="panel p-5"><Bookmark className="text-violet-600" /><p className="mt-4 text-xs text-slate-400">已收藏</p><p className="mt-1 text-2xl font-black text-ink">{favorites.length}<small className="ml-1 text-xs font-medium text-slate-400">个</small></p></Link><Link to="/me/orders" className="panel p-5"><ShoppingBag className="text-amber-600" /><p className="mt-4 text-xs text-slate-400">已购内容</p><p className="mt-1 text-2xl font-black text-ink">{orders.filter(x => x.status === 'paid').length}<small className="ml-1 text-xs font-medium text-slate-400">个</small></p></Link></div><div className="panel p-6"><div className="flex justify-between"><h2 className="font-black text-ink">最近收藏</h2><Link to="/me/favorites" className="text-xs font-bold text-emerald-700">查看全部</Link></div><div className="mt-4 space-y-3">{favorites.slice(0, 3).map(x => <Link key={x.id} to={`/toolkits/${x.id}`} className="flex items-center gap-3 rounded-2xl bg-slate-50 p-3"><img src={x.cover} className="size-14 rounded-xl object-cover" /><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold">{x.title}</p><p className="mt-1 text-xs text-slate-400">{x.category} · {money(x.price)}</p></div><ChevronRight size={16} className="text-slate-300" /></Link>)}{!favorites.length && <p className="py-8 text-center text-sm text-slate-400">还没有收藏内容</p>}</div></div></div>
}

type WalletOperation = { key: string; type: 'recharge' | 'withdraw'; amount?: number }
const walletReceiptKey = (userId: number) => `zhihe_wallet_pending_${userId}`
function readWalletOperation(userId: number): WalletOperation | null {
  try {
    const value = JSON.parse(localStorage.getItem(walletReceiptKey(userId)) || 'null')
    if (!value || value.userId !== userId || !Number.isSafeInteger(userId) || userId <= 0 ||
      typeof value.key !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.key) ||
      !['recharge', 'withdraw'].includes(value.type) || typeof value.amount !== 'number' ||
      !Number.isFinite(value.amount) || value.amount <= 0 || (value.type === 'withdraw' && value.amount < 10) ||
      !Number.isSafeInteger(Math.round(value.amount * 100)) || Math.abs(value.amount * 100 - Math.round(value.amount * 100)) > 1e-6) return null
    return { key: value.key, type: value.type, amount: value.amount }
  } catch { return null }
}
function clearWalletOperation(userId: number, key: string) {
  // Never let an old callback clear another operation's persisted receipt.
  try { if (readWalletOperation(userId)?.key === key) localStorage.removeItem(walletReceiptKey(userId)) }
  catch { toast.warning('请求已确认，但本地标识未清除；再次确认仍会使用原请求键。') }
}

export function WalletPage() {
  const { user, refresh } = useAuth()
  type WalletData = { balance: number; frozenBalance: number; totalIncome: number; transactions: Transaction[]; demoEnabled?: boolean; canPay?: boolean; canRecharge?: boolean; canWithdraw?: boolean; walletMode?: string }
  const remote = useScopedData<WalletData>('/wallet'), data = remote.data, scope = remote.scope
  const [modal, setModal] = useState<'recharge' | 'withdraw' | null>(null), [amount, setAmount] = useState('100'), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false)
  const operation = useRef<WalletOperation | null>(null), inFlight = useRef(false)
  useEffect(() => {
    // Only the authenticated user's receipt is restored; keep it across route,
    // reload and re-login until the original request is definitively confirmed.
    const saved = user ? readWalletOperation(user.id) : null
    operation.current = saved; inFlight.current = false; setModal(null); setBusy(false); setUncertain(!!saved); setAmount(saved ? String(saved.amount) : '100')
  }, [scope.key, user?.id])
  const rechargeEnabled = !!data && data.demoEnabled !== false && data.canRecharge !== false
  const withdrawEnabled = !!data && data.demoEnabled !== false && data.canWithdraw !== false
  const open = (type: 'recharge' | 'withdraw') => {
    if (busy || (type === 'recharge' ? !rechargeEnabled : !withdrawEnabled)) return
    const saved = user ? readWalletOperation(user.id) : null
    if (saved) { operation.current = saved; setUncertain(true); setModal(saved.type); setAmount(String(saved.amount)); return }
    if (uncertain && operation.current) { setModal(operation.current.type); setAmount(String(operation.current.amount)); return }
    operation.current = { key: crypto.randomUUID(), type }; setAmount('100'); setModal(type); setUncertain(false)
  }
  const submit = async () => {
    if (inFlight.current || !user || !modal || !operation.current) return
    const value = Number(amount)
    if (!Number.isFinite(value) || value <= 0 || !Number.isSafeInteger(Math.round(value * 100)) || Math.abs(value * 100 - Math.round(value * 100)) > 1e-6 || (modal === 'withdraw' && value < 10)) { toast.error('请输入有效金额（最多两位小数），提现最低 10 元'); return }
    if (modal === 'recharge' ? !rechargeEnabled : !withdrawEnabled) return
    const op = operation.current
    if (op.amount !== undefined && op.amount !== value) { toast.error('上次请求结果未知，请使用原金额重试'); return }
    op.amount = value
    try { localStorage.setItem(walletReceiptKey(user.id), JSON.stringify({ userId: user.id, key: op.key, type: op.type, amount: value })) }
    catch { toast.error('无法保存请求标识，本次模拟操作未发送；请允许本地存储后重试。'); return }
    inFlight.current = true; setBusy(true)
    const request = scope.begin('wallet-operation')
    try {
      await api(`/wallet/${op.type}`, { method: 'POST', body: JSON.stringify({ amount: value }), headers: { 'Idempotency-Key': op.key }, signal: request.signal })
      if (!request.current()) return
      clearWalletOperation(user.id, op.key); operation.current = null; setUncertain(false); setModal(null)
      toast.success(op.type === 'recharge' ? '模拟充值完成（非真实资金）' : '模拟提现完成（非真实转账）')
      remote.reload(); await refresh().catch(() => {})
    } catch (e) {
      if (!request.current()) return
      if (uncertain || !(e instanceof ApiError) || e.status >= 500) { setUncertain(true); toast.error('请求结果未知，请用原金额重试确认；不会创建新的请求键') }
      else { clearWalletOperation(user.id, op.key); operation.current = { key: crypto.randomUUID(), type: op.type }; setUncertain(false); toast.error(e.message) }
    } finally { if (request.current()) { inFlight.current = false; setBusy(false) } }
  }
  const txIcon = (type: string) => type === 'recharge' ? ArrowDownToLine : type === 'commission' ? BadgeDollarSign : type === 'withdrawal' ? Banknote : type === 'refund' ? ArrowDownToLine : ShoppingBag
  return <><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} /><p className="mb-4 rounded-xl bg-amber-50 p-4 text-sm text-amber-900">演示钱包：充值与提现仅为模拟记录，不涉及真实资金或支付供应商。{data?.demoEnabled === false && ' 当前环境未开放模拟操作。'}</p>{uncertain && <p role="alert" className="mb-4 text-amber-900">上次模拟请求结果未知，请继续以同一金额确认。<button className="btn-ghost" onClick={() => operation.current && open(operation.current.type)}>继续确认</button></p>}<div className="overflow-hidden rounded-3xl bg-gradient-to-br from-ink to-pine p-6 text-white shadow-soft sm:p-8"><div className="flex items-start justify-between"><div><p className="text-xs text-white/45">可用余额（元）</p><p className="mt-2 text-4xl font-black">¥{(data?.balance ?? user?.balance ?? 0).toFixed(2)}</p></div><Coins className="text-mint" size={30} /></div><div className="mt-8 flex gap-3"><button disabled={busy || !rechargeEnabled || (uncertain && operation.current?.type !== 'recharge')} onClick={() => open('recharge')} className="btn-mint"><Plus size={16} />{rechargeEnabled ? '模拟充值' : '充值未开放'}</button><button disabled={busy || !withdrawEnabled || (uncertain && operation.current?.type !== 'withdraw')} onClick={() => open('withdraw')} className="btn border border-white/15 bg-white/10 text-white hover:bg-white/15"><ArrowUpRight size={16} />{withdrawEnabled ? '模拟提现' : '提现未开放'}</button></div><div className="mt-7 flex gap-8 border-t border-white/10 pt-5 text-xs text-white/45"><span>累计分成 <b className="ml-1 text-white">¥{data?.totalIncome.toFixed(2) || '0.00'}</b></span><span>冻结金额 <b className="ml-1 text-white">¥{data?.frozenBalance.toFixed(2) || '0.00'}</b></span></div></div><div className="panel mt-6 p-6"><h2 className="flex items-center gap-2 font-black text-ink"><History size={17} />资金明细</h2><div className="mt-4 divide-y">{data?.transactions.map(tx => { const Icon = txIcon(tx.type); return <div key={tx.id} className="flex items-center gap-3 py-4"><span className={cn('grid size-10 place-items-center rounded-xl', tx.amount > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500')}><Icon size={17} /></span><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{tx.note}</p><p className="mt-1 text-xs text-slate-400">{date(tx.createdAt)}</p></div><p className={cn('font-black', tx.amount > 0 ? 'text-emerald-600' : 'text-ink')}>{tx.amount > 0 && '+'}{tx.amount.toFixed(2)}</p></div>})}{!data?.transactions.length && <p className="py-12 text-center text-sm text-slate-400">暂无资金变动</p>}</div></div><Modal open={!!modal} onClose={() => { if (!busy) setModal(null) }} title={modal === 'recharge' ? '模拟充值（非真实资金）' : '模拟提现（非真实转账）'}><label htmlFor="wallet-amount" className="label">金额（元）</label><div className="relative"><span className="absolute left-4 top-3.5 font-bold">¥</span><input id="wallet-amount" disabled={busy || uncertain} step="0.01" className="input pl-9 text-lg font-black" type="number" min={modal === 'withdraw' ? 10 : 0.01} value={amount} onChange={e => setAmount(e.target.value)} /></div>{modal === 'recharge' && <div className="mt-3 grid grid-cols-3 gap-2">{[50, 100, 500].map(x => <button key={x} disabled={busy || uncertain} onClick={() => setAmount(String(x))} className="rounded-xl bg-slate-100 py-2 text-sm font-bold">¥{x}</button>)}</div>}<p className="mt-4 text-xs leading-5 text-slate-400">{modal === 'recharge' ? '仅更新演示余额，不会收取真实资金。' : '仅更新演示余额，不会转账到银行或支付账户。'}</p><button disabled={busy} onClick={submit} className="btn-mint mt-6 w-full py-3.5">{busy ? '处理中…' : uncertain ? '重试确认（同一请求）' : '确认模拟操作'}</button></Modal></>
}

export function FavoritesPage() {
  const remote = useScopedData<Toolkit[]>('/me/favorites'), items = remote.data ?? []
  return <div><div className="mb-5"><h1 className="text-2xl font-black text-ink">我的收藏</h1><p className="mt-1 text-sm text-slate-400">稍后再看，也不错过好工具</p></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">{items.map(x => <ToolkitCard key={x.id} item={x} />)}</div> : <Empty icon={Heart} title="还没有收藏" detail="逛逛工具库，把感兴趣的内容收藏起来。" action={<Link className="btn-primary" to="/toolkits">探索工具包</Link>} />}</div>
}

export function MyToolkitsPage() {
  const remote = useScopedData<Toolkit[]>('/me/toolkits'), items = remote.data ?? [], scope = remote.scope
  const pending = useRef(new Set<number>()), [busyIds, setBusyIds] = useState<number[]>([])
  useEffect(() => { pending.current.clear(); setBusyIds([]) }, [scope.key])
  const change = async (x: Toolkit, remove: boolean) => {
    if (pending.current.has(x.id)) return
    if (remove && !window.confirm(`确定删除《${x.title}》？已有购买记录的内容将仅下架，并保留已购权益。`)) return
    pending.current.add(x.id); setBusyIds([...pending.current]); const request = scope.begin(`mutation-${x.id}`)
    try {
      if (remove) {
        const result = await api<{ message: string; mode: 'offline' | 'deleted' }>(`/toolkits/${x.id}`, { method: 'DELETE', signal: request.signal })
        if (!request.current()) return
        remote.setData(prev => result.mode === 'deleted' ? prev?.filter(i => i.id !== x.id) : prev?.map(i => i.id === x.id ? { ...i, status: 'offline' } : i))
        toast.success(result.message)
      } else {
        await api(`/toolkits/${x.id}/status`, { method: 'PUT', body: JSON.stringify({ status: 'offline' }), signal: request.signal })
        if (!request.current()) return
        remote.setData(prev => prev?.map(i => i.id === x.id ? { ...i, status: 'offline' } : i)); toast.success('已下架')
      }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { pending.current.delete(x.id); setBusyIds([...pending.current]) } }
  }
  const statusText = (s: string) => s === 'published' ? ['已发布', 'text-emerald-600'] : s === 'offline' ? ['已下架', 'text-red-500'] : ['草稿', 'text-amber-600']
  return <div><div className="mb-5 flex items-end justify-between"><div><h1 className="text-2xl font-black text-ink">我的工具包</h1><p className="mt-1 text-sm text-slate-400">管理你分享的科研方法</p></div><div className="flex gap-2"><Link to="/publish/batch" className="btn-ghost"><Layers size={16} />批量上传</Link><Link to="/publish" className="btn-primary"><Plus size={16} />发布</Link></div></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="space-y-3">{items.map(x => { const [label, color] = statusText(x.status); return <div key={x.id} className="panel flex items-center gap-4 p-4"><Link to={`/toolkits/${x.id}`} className="flex min-w-0 flex-1 items-center gap-4"><img src={x.cover} alt="" className="size-20 rounded-2xl object-cover" /><div className="min-w-0 flex-1"><p className="truncate font-black text-ink">{x.title}</p><div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400"><span>{money(x.price)}</span><span>{x.downloads} 次下载</span><span className={color}>{label}</span></div></div></Link><div className="flex shrink-0 flex-col gap-2 sm:flex-row">{x.status === 'published' && <button disabled={busyIds.includes(x.id)} onClick={() => void change(x, false)} className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-200">下架</button>}<button disabled={busyIds.includes(x.id)} onClick={() => void change(x, true)} className="rounded-lg bg-red-50 px-3 py-2 text-xs font-bold text-red-600">删除</button></div></div>})}</div> : <Empty icon={PackageOpen} title="还没有发布工具包" detail="把验证过的流程整理成教程，帮助更多同行。" action={<Link className="btn-primary" to="/publish">开始发布</Link>} />}</div>
}

export function OrdersPage() {
  const navigate = useNavigate(), remote = useScopedData<Order[]>('/me/orders'), items = remote.data ?? []
  const status = { pending: ['待支付', 'bg-amber-100 text-amber-700'], paid: ['已完成', 'bg-emerald-100 text-emerald-700'], cancelled: ['已取消', 'bg-slate-100 text-slate-500'], refunded: ['已退款', 'bg-slate-100 text-slate-500'] } as const
  return <div><div className="mb-5"><h1 className="text-2xl font-black text-ink">我的订单</h1><p className="mt-1 text-sm text-slate-400">查看购买记录和订单状态</p></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="space-y-4">{items.map(x => <div key={x.id} className="panel p-5"><div className="flex items-center justify-between border-b pb-3 text-xs text-slate-400"><span>{x.orderNo}</span><span className={`chip ${status[x.status][1]}`}>{status[x.status][0]}</span></div><div className="mt-4 flex gap-4"><img src={x.toolkit.cover} className="size-20 rounded-xl object-cover" /><div className="min-w-0 flex-1"><Link to={`/toolkits/${x.toolkit.id}`} className="line-clamp-1 font-bold text-ink">{x.toolkit.title}</Link><p className="mt-2 text-xs text-slate-400">{date(x.createdAt)}</p><p className="mt-2 text-sm font-black">{money(x.amount)}</p></div>{x.status === 'pending' && <button onClick={() => navigate(`/checkout/${x.id}`, { state: { order: x } })} className="btn-mint self-end">去支付</button>}{x.status === 'paid' && <Link to={`/toolkits/${x.toolkit.id}/learn`} className="btn-ghost self-end">去学习</Link>}</div></div>)}</div> : <Empty icon={ClipboardList} title="暂无订单" detail="购买付费工具包后，订单会出现在这里。" action={<Link className="btn-primary" to="/toolkits">看看工具包</Link>} />}</div>
}
