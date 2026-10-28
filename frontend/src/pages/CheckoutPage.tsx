import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, CheckCircle2, CreditCard, ShieldCheck, WalletCards } from 'lucide-react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, money } from '../lib'
import type { Order } from '../types'
import { useRequestScope, useScopedData } from '../useRequestScope'

export function CheckoutPage() {
  const { id } = useParams(), location = useLocation(), navigate = useNavigate()
  const { user, refresh, loading, restoreError } = useAuth(), scope = useRequestScope(id)
  const [order, setOrder] = useState<Order>(), [verifiedKey, setVerifiedKey] = useState('')
  const [busy, setBusy] = useState(false), [missing, setMissing] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const inFlight = useRef(false)
  const wallet = useScopedData<{ canPay?: boolean; demoEnabled?: boolean }>('/wallet')
  const paymentEnabled = !!wallet.data && wallet.data.canPay !== false && wallet.data.demoEnabled !== false
  useEffect(() => {
    const cached = location.state?.order as Order | undefined
    setOrder(cached?.id === Number(id) ? cached : undefined); setVerifiedKey(''); setMissing(false); setBusy(false); setError(''); inFlight.current = false
    if (loading || !user || restoreError) return
    const request = scope.begin()
    void api<Order[]>('/me/orders', { signal: request.signal }).then(rows => {
      if (!request.current()) return
      const found = rows.find(x => x.id === Number(id))
      setOrder(found); setMissing(!found); setVerifiedKey(scope.key)
    }).catch(e => { if (request.current()) setError(e.message) })
  }, [id, scope, loading, user?.id, restoreError, retry])
  if (restoreError) return <div className="p-12 text-center"><p role="alert">会话恢复失败：{restoreError}</p><button className="btn-primary mt-4" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!loading && !user) return <div className="p-12 text-center">请先登录查看订单。<Link className="btn-ghost" to="/me/orders">前往个人中心</Link></div>
  if (error) return <div className="p-12 text-center"><p role="alert">订单读取失败：{error}</p><button className="btn-ghost mt-4" onClick={() => setRetry(x => x + 1)}>重试</button></div>
  if (missing && verifiedKey === scope.key) return <div className="p-12 text-center text-slate-400"><p>未找到该订单，可能不属于当前账号。</p><Link to="/me/orders" className="btn-ghost mt-4">返回订单列表</Link></div>
  if (!order || verifiedKey !== scope.key) return <div className="p-12 text-center text-slate-400">正在读取订单…</div>
  const unavailable = order.status === 'pending' && (order.canPay === false || (order.toolkit.status !== undefined && order.toolkit.status !== 'published'))
  const pay = async () => {
    if (inFlight.current || order.status !== 'pending' || unavailable || !paymentEnabled) return
    inFlight.current = true; setBusy(true); const request = scope.begin('payment')
    try {
      await api(`/orders/${order.id}/pay`, { method: 'POST', signal: request.signal })
      if (!request.current()) return
      await refresh().catch(() => {})
      if (!request.current()) return
      toast.success('支付成功，工具包已解锁'); navigate(`/toolkits/${order.toolkit.id}/learn`)
    } catch (e) { if (request.current()) { toast.error((e as Error).message); setRetry(x => x + 1) } }
    finally { if (request.current()) { inFlight.current = false; setBusy(false) } }
  }
  const cancel = async () => {
    if (inFlight.current || order.status !== 'pending') return
    inFlight.current = true; setBusy(true); const request = scope.begin('payment')
    try {
      await api(`/orders/${order.id}/cancel`, { method: 'POST', signal: request.signal })
      if (request.current()) { setOrder(prev => prev && { ...prev, status: 'cancelled' }); toast.success('订单已取消') }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { inFlight.current = false; setBusy(false) } }
  }
  return <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:py-14"><Link to={`/toolkits/${order.toolkit.id}`} className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回工具包</Link><div className="mt-6 panel overflow-hidden"><div className="bg-ink p-6 text-white sm:p-8"><span className="chip bg-mint text-ink">确认订单</span><h1 className="mt-4 text-3xl font-black">距离解锁，只差一步</h1><p className="mt-2 text-sm text-white/50">请确认订单信息并使用钱包余额完成支付</p></div><div className="p-6 sm:p-8"><div className="flex gap-4 rounded-2xl bg-slate-50 p-4"><img src={order.toolkit.cover} className="size-20 rounded-xl object-cover" /><div className="min-w-0 flex-1"><p className="truncate font-black text-ink">{order.toolkit.title}</p><p className="mt-2 text-xs text-slate-400">创作者：{order.seller}</p><p className="mt-2 text-xs text-slate-400">永久访问 · 含全部资源</p></div><p className="font-black text-ink">{money(order.amount)}</p></div><div className="mt-6 space-y-3 border-y py-5 text-sm"><div className="flex justify-between text-slate-500"><span>订单编号</span><span className="font-mono text-xs">{order.orderNo}</span></div><div className="flex justify-between text-slate-500"><span>商品金额</span><span>{money(order.amount)}</span></div><div className="flex justify-between text-lg font-black text-ink"><span>应付金额</span><span>{money(order.amount)}</span></div></div><div className="mt-6 rounded-2xl border-2 border-pine bg-emerald-50/50 p-4"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-pine text-mint"><WalletCards size={19} /></span><div className="flex-1"><p className="font-bold text-ink">钱包余额</p><p className="text-xs text-slate-500">当前可用 {money(user?.balance || 0)}</p></div><CheckCircle2 className="text-emerald-600" size={20} /></div></div>{(user?.balance || 0) < order.amount && <div className="mt-4 flex items-center justify-between rounded-xl bg-amber-50 p-3 text-sm text-amber-800"><span>余额不足，还差 {money(order.amount - (user?.balance || 0))}</span><Link to="/me/wallet" className="font-bold underline">去充值</Link></div>}{wallet.data && !paymentEnabled && <p role="alert" className="mt-4 text-amber-800">当前未开放模拟交易，尚未接入真实支付。待付款订单仍可取消。</p>}{wallet.error && <p role="alert" className="mt-4 text-red-600">支付能力读取失败：{wallet.error}<button className="btn-ghost" onClick={wallet.reload}>重试</button></p>}{unavailable && <p role="alert" className="mt-4 text-amber-800">工具包已下架或不可售，不能付款；你仍可取消待付款订单。</p>}{order.status !== 'pending' && <p className="mt-4">订单状态：{order.status === 'paid' ? '已支付' : order.status === 'cancelled' ? '已取消' : '历史已退款（只读）'}</p>}<button disabled={busy || !paymentEnabled || unavailable || (user?.balance || 0) < order.amount || order.status !== 'pending'} onClick={pay} className="btn-mint mt-6 w-full py-4"><CreditCard size={18} />{busy ? '正在支付…' : `确认支付 ${money(order.amount)}`}</button>{order.status === 'pending' && <button disabled={busy} className="btn-ghost mt-3 w-full" onClick={() => void cancel()}>取消订单</button>}<p className="mt-4 text-center text-xs text-slate-400"><ShieldCheck size={13} className="mr-1 inline" />余额支付受平台保护，付款后立即解锁</p></div></div></div>
}
