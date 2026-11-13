import { Search } from 'lucide-react'

// Shared filter strip for the admin tables: a search box, a status/role
// select, and a live result hint. Submitting resets the caller back to page 1
// (handled by the owner through the onQuery callback).
export function AdminToolbar({ search, onSearch, status, statuses, onStatus, placeholder }: {
  search: string; onSearch: (value: string) => void
  status: string; statuses: [string, string][]; onStatus: (value: string) => void
  placeholder: string
}) {
  return <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 p-4 sm:px-6">
    <div className="flex min-w-[200px] flex-1 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 focus-within:border-emerald-600">
      <Search size={16} className="text-slate-400" />
      <input value={search} aria-label="搜索关键词" placeholder={placeholder}
        onChange={event => onSearch(event.target.value)}
        className="w-full bg-transparent text-sm text-slate-700 outline-none placeholder:text-slate-400" />
    </div>
    <div className="flex items-center gap-1.5" role="group" aria-label="状态筛选">
      <button type="button" onClick={() => onStatus('')} aria-pressed={status === ''}
        className={pill(status === '')}>全部</button>
      {statuses.map(([value, label]) => <button key={value} type="button" onClick={() => onStatus(value)}
        aria-pressed={status === value} className={pill(status === value)}>{label}</button>)}
    </div>
  </div>
}

const pill = (active: boolean) => `rounded-lg px-3 py-1.5 text-sm font-semibold transition ${active ? 'bg-ink text-white' : 'bg-white text-slate-500 ring-1 ring-slate-200 hover:text-ink'}`
