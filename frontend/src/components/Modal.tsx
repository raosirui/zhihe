import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'

export function Modal({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const titleId = useId(), dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.focus()
    return () => previous?.focus()
  }, [open])
  if (!open) return null
  return <div className="fixed inset-0 z-[100] grid place-items-end bg-ink/50 p-0 backdrop-blur-sm sm:place-items-center sm:p-6" onMouseDown={onClose}>
    <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={e => { if (e.key === 'Escape') onClose(); if (e.key === 'Tab') { const nodes = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]'); if (nodes?.length) { const first = nodes[0], last = nodes[nodes.length - 1]; if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last.focus() } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() } } } }} className={`max-h-[92vh] w-full overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl animate-enter sm:rounded-3xl sm:p-7 ${wide ? 'sm:max-w-2xl' : 'sm:max-w-md'}`} onMouseDown={e => e.stopPropagation()}>
      <div className="mb-6 flex items-center justify-between">
        <h2 id={titleId} className="text-xl font-black text-ink">{title}</h2>
        <button aria-label="关闭对话框" type="button" onClick={onClose} className="grid size-9 place-items-center rounded-full bg-slate-100 text-slate-500 hover:bg-slate-200"><X size={18} /></button>
      </div>
      {children}
    </div>
  </div>
}

