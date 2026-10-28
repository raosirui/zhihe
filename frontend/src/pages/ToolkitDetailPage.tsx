import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Bookmark, BookOpen, CheckCircle2, Clock3, Download, FileArchive, LockKeyhole, PlayCircle, ShieldCheck, Star } from 'lucide-react'
import { Link, useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, cn, downloadAuthed, money } from '../lib'
import type { Order, Toolkit } from '../types'
import { useRequestScope, useScopedData } from '../useRequestScope'
import { Empty } from '../components/Empty'

export function ToolkitDetailPage() {
  const { id } = useParams(); const navigate = useNavigate(); const { user, loading } = useAuth(); const { openAuth } = useOutletContext<{ openAuth: () => void }>()
  const scope = useRequestScope(id)
  const [loaded, setLoaded] = useState<{ key: string; item: Toolkit }>(), [busy, setBusy] = useState(false), [favoriteBusy, setFavoriteBusy] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const buying = useRef(false), favoriting = useRef(false)
  const wallet = useScopedData<{ canPay?: boolean; demoEnabled?: boolean }>('/wallet')
  const purchaseEnabled = !user || (!!wallet.data && wallet.data.canPay !== false && wallet.data.demoEnabled !== false)
  useEffect(() => {
    setLoaded(undefined); setError(''); setBusy(false); setFavoriteBusy(false); buying.current = false; favoriting.current = false
    if (loading) return
    const request = scope.begin()
    void api<Toolkit>(`/toolkits/${id}`, { signal: request.signal }).then(item => { if (request.current()) setLoaded({ key: scope.key, item }) }).catch(e => { if (request.current()) setError(e.message) })
  }, [id, scope, loading, retry])
  if (error) return <Empty title="工具包读取失败" detail={error} action={<button className="btn-primary" onClick={() => setRetry(x => x + 1)}>重试</button>} />
  const item = loaded?.key === scope.key ? loaded.item : undefined
  if (!item) return <div className="mx-auto max-w-6xl animate-pulse px-4 py-10"><div className="h-[500px] rounded-3xl bg-slate-200" /></div>
  const unlocked = item.canLearn ?? (item.price === 0 || item.purchased || item.author.id === user?.id || user?.role === 'admin')
  const downloadable = user ? (item.canDownload ?? unlocked) : (unlocked || item.price === 0)
  const preview = item.lessons?.find(lesson => lesson.isPreview && !lesson.locked)
  const favorite = async () => {
    if (!user) return openAuth()
    if (favoriting.current) return
    favoriting.current = true; setFavoriteBusy(true); const request = scope.begin('favorite')
    try {
      const result = await api<{ favorited: boolean }>(`/toolkits/${item.id}/favorite`, { method: 'POST', signal: request.signal })
      if (request.current()) { setLoaded(prev => prev && { ...prev, item: { ...prev.item, favorited: result.favorited } }); toast.success(result.favorited ? '已加入收藏' : '已取消收藏') }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { favoriting.current = false; setFavoriteBusy(false) } }
  }
  const buy = async () => {
    if (!user) return openAuth()
    if (buying.current || item.status !== 'published' || !purchaseEnabled) return
    buying.current = true; setBusy(true); const request = scope.begin('buy')
    try {
      const order = await api<Order>('/orders', { method: 'POST', body: JSON.stringify({ toolkitId: item.id }), signal: request.signal })
      if (request.current()) navigate(`/checkout/${order.id}`, { state: { order } })
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { buying.current = false; setBusy(false) } }
  }
  const start = () => { if (unlocked) navigate(`/toolkits/${item.id}/learn`); else void buy() }
  const download = async () => {
    if (!user) return openAuth()
    const request = scope.begin('download')
    try { await downloadAuthed(`/toolkits/${item.id}/download`, item.resourceName || 'toolkit-resource', request) }
    catch (e) { if (request.current()) toast.error((e as Error).message) }
  }
  return <div className="pb-12">
    <div className="bg-ink text-white"><div className="mx-auto max-w-[1240px] px-4 py-6 sm:px-6 lg:px-8"><Link to="/toolkits" className="inline-flex items-center gap-2 text-sm text-white/50 hover:text-white"><ArrowLeft size={16} />返回工具库</Link><div className="mt-7 grid gap-9 pb-8 lg:grid-cols-[1.1fr_.9fr] lg:items-center">
      <div><div className="flex flex-wrap gap-2"><span className="chip bg-mint text-ink">{item.category}</span><span className="chip bg-white/10 text-white/70">{item.level}</span>{item.featured && <span className="chip bg-amber-400/15 text-amber-300">精选工具包</span>}</div><h1 className="mt-5 text-3xl font-black leading-tight sm:text-5xl">{item.title}</h1><p className="mt-5 max-w-2xl text-base leading-8 text-white/60">{item.summary}</p><div className="mt-6 flex flex-wrap items-center gap-5 text-sm text-white/50"><span className="font-semibold text-white">{item.author.nickname}</span><span><Star size={15} className="mr-1 inline fill-amber-400 text-amber-400" />{item.rating}</span><span><Download size={15} className="mr-1 inline" />{item.downloads} 次下载</span><span><BookOpen size={15} className="mr-1 inline" />{item.lessonCount} 节教程</span></div></div>
      <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/5 p-2"><img src={item.cover} className="aspect-video w-full rounded-[1.15rem] object-cover opacity-80" alt="" /><div className="absolute inset-2 bg-gradient-to-t from-ink/60 to-transparent" /><button disabled={busy || (!unlocked && !preview && !purchaseEnabled)} aria-label={unlocked ? '开始学习工具包' : preview ? '试看教程' : '购买工具包'} onClick={() => preview && !unlocked ? navigate(`/toolkits/${item.id}/learn?lesson=${preview.id}`) : start()} className="absolute inset-0 m-auto grid size-16 place-items-center rounded-full bg-mint text-ink shadow-glow transition hover:scale-105"><PlayCircle size={28} /></button></div>
    </div></div></div>
    <div className="mx-auto grid max-w-[1240px] gap-7 px-4 py-8 sm:px-6 lg:grid-cols-[1fr_350px] lg:px-8">
      <div className="space-y-7"><section className="panel p-6 sm:p-8"><h2 className="text-xl font-black text-ink">工具包介绍</h2><div className="mt-4 whitespace-pre-line text-sm leading-8 text-slate-600">{item.description}</div><div className="mt-6 flex flex-wrap gap-2">{item.tags.map(x => <span key={x} className="chip bg-slate-100 text-slate-500">#{x}</span>)}</div></section>
        <section className="panel p-6 sm:p-8"><div className="flex items-center justify-between"><h2 className="text-xl font-black text-ink">学习目录</h2><span className="text-xs text-slate-400">约 {item.lessons?.reduce((a, x) => a + x.duration, 0) ?? 0} 分钟</span></div><div className="mt-5 divide-y">{item.lessons?.map((lesson, index) => <div key={lesson.id} className="flex items-center gap-4 py-4"><span className={cn('grid size-9 shrink-0 place-items-center rounded-xl text-xs font-black', !lesson.locked ? 'bg-mint text-ink' : 'bg-slate-100 text-slate-400')}>{String(index + 1).padStart(2, '0')}</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-slate-700">{lesson.title}</p><p className="mt-1 text-xs text-slate-400"><Clock3 size={12} className="mr-1 inline" />{lesson.duration} 分钟 {lesson.isPreview && <span className="ml-2 text-emerald-700">可试看</span>}</p></div>{lesson.locked ? <LockKeyhole size={16} className="text-slate-300" /> : <Link className="btn-ghost text-xs" to={`/toolkits/${item.id}/learn?lesson=${lesson.id}`}>{lesson.isPreview && !unlocked ? '试看' : '学习'}<CheckCircle2 size={17} className="text-emerald-600" /></Link>}</div>)}</div></section>
        <section className="panel p-6 sm:p-8"><div className="flex items-center gap-4"><span className="grid size-12 place-items-center rounded-2xl bg-emerald-100 text-emerald-700"><FileArchive size={22} /></span><div className="flex-1"><h2 className="font-black text-ink">配套资源</h2><p className="mt-1 text-xs text-slate-400">{item.resourceName || '作者尚未上传可下载文件'}</p></div><button disabled={!downloadable || !item.resourceName} onClick={download} className="btn-ghost"><Download size={16} />下载</button></div></section>
      </div>
      <aside><div className="panel sticky top-24 p-6"><p className="text-xs font-bold text-slate-400">完整工具包</p><div className="mt-2 text-3xl font-black text-ink">{money(item.price)}</div>{!unlocked && <p className="mt-2 text-xs text-slate-400">购买后永久解锁完整教程和配套资源</p>}{!unlocked && wallet.data && !purchaseEnabled && <p role="alert" className="mt-4 text-sm text-amber-800">当前未开放模拟交易，尚未接入真实支付；可试看已开放教程。</p>}{!unlocked && wallet.error && <p role="alert" className="mt-4 text-sm text-red-600">购买能力读取失败<button className="btn-ghost" onClick={wallet.reload}>重试</button></p>}<button disabled={busy || (!unlocked && !purchaseEnabled)} onClick={start} className="btn-mint mt-6 w-full py-3.5">{unlocked ? '开始学习' : busy ? '正在创建订单…' : '立即购买'} <ArrowRight size={17} /></button>{preview && !unlocked && <Link className="btn-primary mt-3 w-full" to={`/toolkits/${item.id}/learn?lesson=${preview.id}`}><PlayCircle size={16} />免费试看</Link>}<button disabled={favoriteBusy} onClick={favorite} className="btn-ghost mt-3 w-full"><Bookmark size={16} className={item.favorited ? 'fill-pine text-pine' : ''} />{item.favorited ? '已收藏' : '收藏工具包'}</button><div className="mt-6 space-y-3 border-t pt-5 text-xs text-slate-500"><p><ShieldCheck size={15} className="mr-2 inline text-emerald-600" />钱包余额结算，平台暂不支持退款</p><p><BookOpen size={15} className="mr-2 inline text-emerald-600" />已购内容下架后仍可学习</p><p><Download size={15} className="mr-2 inline text-emerald-600" />配套资源一键下载</p></div></div></aside>
    </div>
  </div>
}
