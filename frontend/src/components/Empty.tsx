import type { LucideIcon } from 'lucide-react'

export function Empty({ icon: Icon, title, detail, action }: { icon: LucideIcon; title: string; detail: string; action?: React.ReactNode }) {
  return <div className="panel flex min-h-72 flex-col items-center justify-center p-8 text-center"><span className="grid size-14 place-items-center rounded-2xl bg-slate-100 text-slate-400"><Icon size={25} /></span><h3 className="mt-4 font-black text-ink">{title}</h3><p className="mt-2 max-w-sm text-sm text-slate-400">{detail}</p>{action && <div className="mt-5">{action}</div>}</div>
}
