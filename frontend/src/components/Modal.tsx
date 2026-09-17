import { X } from 'lucide-react'
import type { ReactNode } from 'react'

export function Modal({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  if (!open) return null
  return <div className="fixed inset-0 z-[100] grid place-items-end bg-ink/50 p-0 backdrop-blur-sm sm:place-items-center sm:p-6" onMouseDown={onClose}>
    <div className={`max-h-[92vh] w-full overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl animate-enter sm:rounded-3xl sm:p-7 ${wide ? 'sm:max-w-2xl' : 'sm:max-w-md'}`} onMouseDown={e => e.stopPropagation()}>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-black text-ink">{title}</h2>
        <button onClick={onClose} className="grid size-9 place-items-center rounded-full bg-slate-100 text-slate-500 hover:bg-slate-200"><X size={18} /></button>
      </div>
      {children}
    </div>
  </div>
}

