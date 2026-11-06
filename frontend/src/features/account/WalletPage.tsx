import { useEffect, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowUpRight, BadgeDollarSign, Banknote, Coins, History, Plus, ShoppingBag } from 'lucide-react'
import { toast } from 'sonner'
import { useAuth } from '../../AuthContext'
import { Modal } from '../../components/Modal'
import { api, ApiError } from '../../shared/api'
import { cn, date } from '../../shared/lib'
import { useScopedData } from '../../useRequestScope'
import type { Transaction } from '../../types'
import { LoadState } from './LoadState'

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
