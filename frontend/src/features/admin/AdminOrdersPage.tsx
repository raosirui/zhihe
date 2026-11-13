import { useState } from 'react'
import { cn, money } from '../../shared/lib'
import { AdminShell } from './AdminShell'
import { AdminToolbar } from './AdminToolbar'
import { Pagination } from '../../components/Pagination'
import { useAdminPaged } from './useAdminPaged'
import { PER_PAGE_DEFAULT } from './constants'
import type { Order } from '../../types'

const orderStatus: Record<string, [string, string]> = {
  pending: ['待支付', 'bg-amber-50 text-amber-700'],
  paid: ['已支付', 'bg-emerald-50 text-emerald-700'],
  cancelled: ['已取消', 'bg-slate-100 text-slate-500'],
  refunded: ['已退款', 'bg-red-50 text-red-600'],
}

export function AdminOrdersPage() {
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(PER_PAGE_DEFAULT)
  const query = new URLSearchParams({ page: String(page), per_page: String(perPage) })
  if (status) query.set('status', status)
  if (search.trim()) query.set('q', search.trim())
  const { data, loading, error, reload } = useAdminPaged<Order>('/admin/orders', query.toString())
  const items = data?.items ?? []
  const reset = () => setPage(1)

  return <AdminShell title="订单管理" subtitle="平台交易流水（只读）。不支持退款，历史退款记录仅作查阅">
    <div className="panel overflow-hidden">
      <AdminToolbar search={search} onSearch={value => { setSearch(value); reset() }}
        status={status} onStatus={value => { setStatus(value); reset() }}
        statuses={[['paid', '已支付'], ['pending', '待支付'], ['cancelled', '已取消'], ['refunded', '已退款']]} placeholder="搜索订单号 / 商品 / 买家" />
      {error && <p role="alert" className="p-4 text-sm text-red-600">{error}<button className="btn-ghost ml-3" onClick={reload}>重试</button></p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-slate-400"><tr>
            {['订单号', '买家 → 卖家', '商品', '金额', '佣金', '状态', '下单时间'].map(h => <th key={h} className="whitespace-nowrap px-4 py-3 font-bold first:px-6">{h}</th>)}
          </tr></thead>
          <tbody className="divide-y">
            {loading && !items.length ? <tr><td colSpan={7} className="p-10 text-center text-sm text-slate-400">正在加载…</td></tr>
              : !items.length ? <tr><td colSpan={7} className="p-10 text-center text-sm text-slate-400">没有匹配的订单</td></tr>
              : items.map(o => <tr key={o.id}>
                <td className="whitespace-nowrap px-6 py-3 font-mono text-xs text-slate-500">{o.orderNo}</td>
                <td className="whitespace-nowrap px-4 py-3 text-xs">{o.buyer || '—'} → {o.seller}</td>
                <td className="max-w-[220px] truncate px-4 py-3"><a href={`/toolkits/${o.toolkit.id}`} className="text-ink hover:text-emerald-700">{o.toolkit.title}</a></td>
                <td className="whitespace-nowrap px-4 py-3 font-bold text-ink">{money(o.amount)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">{money(o.commission)}</td>
                <td className="whitespace-nowrap px-4 py-3"><span className={cn('chip', (orderStatus[o.status] || ['未知', 'bg-slate-100 text-slate-500'])[1])}>{(orderStatus[o.status] || ['未知'])[0]}</span></td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">{new Date(o.createdAt + 'Z').toLocaleString('zh-CN', { hour12: false })}</td>
              </tr>)}
          </tbody>
        </table>
      </div>
      <Pagination page={data?.page ?? page} pages={data?.pages ?? 0} total={data?.total ?? 0} perPage={perPage} disabled={loading}
        onPage={setPage} onPerPage={size => { setPerPage(size); reset() }} />
    </div>
  </AdminShell>
}
