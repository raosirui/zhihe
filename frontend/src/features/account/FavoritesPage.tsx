import { Heart } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Empty } from '../../components/Empty'
import { ToolkitCard } from '../../components/ToolkitCard'
import { useScopedData } from '../../useRequestScope'
import type { Toolkit } from '../../types'
import { LoadState } from './LoadState'

export function FavoritesPage() {
  const remote = useScopedData<Toolkit[]>('/me/favorites'), items = remote.data ?? []
  return <div><div className="mb-5"><h1 className="text-2xl font-black text-ink">我的收藏</h1><p className="mt-1 text-sm text-slate-400">稍后再看，也不错过好工具</p></div><LoadState loading={remote.loading} error={remote.error} retry={remote.reload} />{items.length ? <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">{items.map(x => <ToolkitCard key={x.id} item={x} />)}</div> : <Empty icon={Heart} title="还没有收藏" detail="逛逛工具库，把感兴趣的内容收藏起来。" action={<Link className="btn-primary" to="/toolkits">探索工具包</Link>} />}</div>
}
