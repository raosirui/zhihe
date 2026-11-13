import { useState } from 'react'
import { ShieldCheck, User as UserIcon } from 'lucide-react'
import { cn, money } from '../../shared/lib'
import { AdminShell } from './AdminShell'
import { AdminToolbar } from './AdminToolbar'
import { Pagination } from '../../components/Pagination'
import { useAdminPaged } from './useAdminPaged'
import { PER_PAGE_DEFAULT } from './constants'

export type AdminUser = {
  id: number; username: string; email?: string | null; nickname: string; role: 'admin' | 'user'
  balance: number; frozenBalance: number; createdAt: string
  published: number; income: number; spent: number; ratings: number
}

export function AdminUsersPage() {
  const [role, setRole] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(PER_PAGE_DEFAULT)
  const query = new URLSearchParams({ page: String(page), per_page: String(perPage) })
  if (role) query.set('role', role)
  if (search.trim()) query.set('q', search.trim())
  const { data, loading, error, reload } = useAdminPaged<AdminUser>('/admin/users', query.toString())
  const items = data?.items ?? []
  const reset = () => setPage(1)

  return <AdminShell title="用户管理" subtitle="注册用户检索：角色、发布内容、账户余额与消费情况">
    <div className="panel overflow-hidden">
      <AdminToolbar search={search} onSearch={value => { setSearch(value); reset() }}
        status={role} onStatus={value => { setRole(value); reset() }}
        statuses={[['admin', '管理员'], ['user', '普通用户']]} placeholder="搜索用户名 / 昵称 / 邮箱" />
      {error && <p role="alert" className="p-4 text-sm text-red-600">{error}<button className="btn-ghost ml-3" onClick={reload}>重试</button></p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-slate-400"><tr>
            {['用户', '角色', '余额 / 冻结', '已上架', '累计收入', '累计消费', '评分', '注册时间'].map(h => <th key={h} className="whitespace-nowrap px-4 py-3 font-bold first:px-6">{h}</th>)}
          </tr></thead>
          <tbody className="divide-y">
            {loading && !items.length ? <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">正在加载…</td></tr>
              : !items.length ? <tr><td colSpan={8} className="p-10 text-center text-sm text-slate-400">没有匹配的用户</td></tr>
              : items.map(u => <tr key={u.id}>
                <td className="px-6 py-3"><div className="flex items-center gap-3">
                  <span className={cn('grid size-9 shrink-0 place-items-center rounded-full text-xs font-black', u.role === 'admin' ? 'bg-ink text-mint' : 'bg-slate-100 text-slate-500')}>
                    {u.role === 'admin' ? <ShieldCheck size={15} /> : <UserIcon size={15} />}</span>
                  <div className="min-w-0"><p className="truncate font-bold text-ink">{u.nickname}<span className="ml-1.5 text-xs font-normal text-slate-400">@{u.username}</span></p>
                    <p className="truncate text-xs text-slate-400">{u.email || '无邮箱'}</p></div>
                </div></td>
                <td className="whitespace-nowrap px-4 py-3"><span className={cn('chip', u.role === 'admin' ? 'bg-pine text-mint' : 'bg-slate-100 text-slate-500')}>{u.role === 'admin' ? '超级管理员' : '普通用户'}</span></td>
                <td className="whitespace-nowrap px-4 py-3 font-semibold text-ink">{money(u.balance)}<span className="ml-1 text-xs font-normal text-slate-400">/ {money(u.frozenBalance)}</span></td>
                <td className="px-4 py-3 text-slate-600">{u.published}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">{money(u.income)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-600">{money(u.spent)}</td>
                <td className="px-4 py-3 text-slate-600">{u.ratings}</td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-400">{new Date(u.createdAt + 'Z').toLocaleDateString('zh-CN')}</td>
              </tr>)}
          </tbody>
        </table>
      </div>
      <Pagination page={data?.page ?? page} pages={data?.pages ?? 0} total={data?.total ?? 0} perPage={perPage} disabled={loading}
        onPage={setPage} onPerPage={size => { setPerPage(size); reset() }} />
    </div>
  </AdminShell>
}
