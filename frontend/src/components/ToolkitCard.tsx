import { Bookmark, Download, LockKeyhole, Star } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Toolkit } from '../types'
import { money } from '../lib'

export function ToolkitCard({ item }: { item: Toolkit }) {
  return <Link to={`/toolkits/${item.id}`} className="group overflow-hidden rounded-3xl border border-slate-200/80 bg-white shadow-sm transition duration-300 hover:-translate-y-1 hover:shadow-soft">
    <div className="relative aspect-[16/10] overflow-hidden bg-pine">
      {item.cover ? <img src={item.cover} alt="" className="h-full w-full object-cover transition duration-500 group-hover:scale-105" /> : <div className="h-full bg-gradient-to-br from-pine to-emerald-800" />}
      <div className="absolute inset-0 bg-gradient-to-t from-ink/70 via-transparent to-transparent" />
      <span className={`absolute left-3 top-3 chip ${item.price === 0 ? 'bg-mint text-ink' : 'bg-white/90 text-ink'}`}>{money(item.price)}</span>
      {item.purchased && item.price > 0 && <span className="absolute right-3 top-3 chip bg-emerald-500 text-white">已解锁</span>}
      <div className="absolute bottom-3 left-3 flex items-center gap-2 text-xs font-medium text-white/80"><span>{item.category}</span><span>·</span><span>{item.level}</span></div>
    </div>
    <div className="p-5">
      <h3 className="line-clamp-1 text-lg font-black text-ink group-hover:text-emerald-800">{item.title}</h3>
      <p className="mt-2 line-clamp-2 min-h-10 text-sm leading-5 text-slate-500">{item.summary}</p>
      <div className="mt-4 flex items-center justify-between border-t pt-4 text-xs text-slate-400">
        <span className="font-semibold text-slate-600">{item.author.nickname}</span>
        <span className="flex items-center gap-3"><span><Star size={13} className="mr-1 inline fill-amber-400 text-amber-400" />{item.rating}</span><span><Download size={13} className="mr-1 inline" />{item.downloads}</span>{item.favorited && <Bookmark size={13} className="fill-pine text-pine" />}{!item.purchased && item.price > 0 && <LockKeyhole size={13} />}</span>
      </div>
    </div>
  </Link>
}

