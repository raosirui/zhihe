import { Dna } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cn } from '../lib'

export function Logo({ light = false, compact = false }: { light?: boolean; compact?: boolean }) {
  return <Link to="/" className="flex items-center gap-2.5">
    <span className={cn('grid size-10 place-items-center rounded-xl', light ? 'bg-mint text-ink' : 'bg-ink text-mint')}><Dna size={21} /></span>
    {!compact && <span className={cn('text-xl font-black tracking-tight', light ? 'text-white' : 'text-ink')}>知核 <span className="ml-1 hidden text-xs font-medium tracking-widest opacity-50 sm:inline">ZHIHE</span></span>}
  </Link>
}
