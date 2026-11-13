import { useState } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { cn } from '../shared/lib'

export const PAGE_SIZES = [10, 20, 50]
const base = 'inline-flex min-h-[2rem] items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-sm font-semibold text-slate-600 transition hover:bg-slate-50 disabled:pointer-events-none disabled:opacity-40'

// Windowed page numbers: always show first, last and the neighbours of the
// current page, collapsing the gaps into an ellipsis so long catalogues stay
// compact while every jump is one click away.
export function pageItems(page: number, pages: number): (number | 'gap')[] {
  if (pages <= 1) return pages === 1 ? [1] : []
  const keep = new Set<number>([1, pages, page, page - 1, page + 1])
  if (page <= 4) [2, 3, 4].forEach(n => keep.add(n))
  if (page >= pages - 3) [pages - 3, pages - 2, pages - 1].forEach(n => keep.add(n))
  const nums = [...keep].filter(n => n >= 1 && n <= pages).sort((a, b) => a - b)
  const out: (number | 'gap')[] = []
  let prev = 0
  for (const n of nums) { if (prev && n - prev > 1) out.push('gap'); out.push(n); prev = n }
  return out
}

export function Pagination({ page, pages, total, perPage, onPage, onPerPage, disabled }: {
  page: number; pages: number; total: number; perPage: number
  onPage: (page: number) => void; onPerPage: (size: number) => void; disabled?: boolean
}) {
  const [jump, setJump] = useState('')
  if (pages <= 0) return null
  const go = (next: number) => onPage(Math.min(pages, Math.max(1, next)))
  const submit = () => { const n = Number.parseInt(jump, 10); if (Number.isFinite(n) && n >= 1) go(n); setJump('') }
  return <nav aria-label="分页导航" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-slate-100 bg-slate-50/40 px-4 py-3 sm:px-6">
    <p className="text-xs text-slate-400">共 <b className="text-ink">{total}</b> 条 · 第 {page} / {pages} 页</p>
    <div className="order-3 flex flex-wrap items-center gap-1 sm:order-2">
      <button type="button" className={base} aria-label="第一页" disabled={disabled || page <= 1} onClick={() => go(1)}><ChevronsLeft size={15} /></button>
      <button type="button" className={base} aria-label="上一页" disabled={disabled || page <= 1} onClick={() => go(page - 1)}><ChevronLeft size={16} />上一页</button>
      {pageItems(page, pages).map((item, index) => item === 'gap'
        ? <span key={`gap-${index}`} aria-hidden className="px-1.5 text-slate-400">…</span>
        : <button key={item} type="button" aria-current={item === page ? 'page' : undefined}
            onClick={() => go(item)} className={cn(base, 'min-w-[2rem] px-2', item === page && 'border-ink bg-ink text-white hover:bg-ink')}>{item}</button>)}
      <button type="button" className={base} aria-label="下一页" disabled={disabled || page >= pages} onClick={() => go(page + 1)}>下一页<ChevronRight size={16} /></button>
      <button type="button" className={base} aria-label="最后一页" disabled={disabled || page >= pages} onClick={() => go(pages)}><ChevronsRight size={15} /></button>
    </div>
    <div className="order-2 flex flex-wrap items-center gap-2 text-xs text-slate-500 sm:order-3">
      <label className="flex items-center gap-1.5">每页
        <select aria-label="每页条数" value={perPage} disabled={disabled} onChange={event => onPerPage(Number(event.target.value))}
          className="rounded-lg border border-slate-200 bg-white px-2 py-1 font-semibold text-slate-600 focus:outline focus:outline-2 focus:outline-emerald-700">
          {PAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
        </select>条
      </label>
      <span className="flex items-center gap-1">跳至
        <input aria-label="跳转到页码" value={jump} inputMode="numeric" disabled={disabled}
          onChange={event => setJump(event.target.value.replace(/\D/g, ''))} onKeyDown={event => { if (event.key === 'Enter') submit() }}
          className="w-14 rounded-lg border border-slate-200 bg-white px-2 py-1 text-center font-semibold text-slate-700 focus:outline focus:outline-2 focus:outline-emerald-700" />页
        <button type="button" className={base} disabled={disabled || !jump} onClick={submit}>跳转</button>
      </span>
    </div>
  </nav>
}
