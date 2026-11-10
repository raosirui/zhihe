import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Bookmark, BookOpen, CheckCircle2, Clock3, Download, Eye, FileArchive, LockKeyhole, PlayCircle, ShieldCheck, Star } from 'lucide-react'
import { Link, useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { useAuth } from '../../AuthContext'
import { api, downloadAuthed, fetchAuthedBlob } from '../../shared/api'
import { cn, money } from '../../shared/lib'
import type { Order, Toolkit, ToolkitResource } from '../../types'
import { useRequestScope, useScopedData } from '../../useRequestScope'
import { Empty } from '../../components/Empty'
import { DocumentRenderer } from '../../components/DocumentRenderer'
import { ProtectedImage } from '../../components/ProtectedImage'
import { SelectedFilePreview } from '../../components/SelectedFilePreview'

export function ToolkitDetailPage() {
  const { id } = useParams(); const navigate = useNavigate(); const { user, loading } = useAuth(); const { openAuth } = useOutletContext<{ openAuth: () => void }>()
  const scope = useRequestScope(id)
  const [loaded, setLoaded] = useState<{ key: string; item: Toolkit }>(), [busy, setBusy] = useState(false), [favoriteBusy, setFavoriteBusy] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const [myScore, setMyScore] = useState(0), [scoreBusy, setScoreBusy] = useState(false)
  const [resourcePreview, setResourcePreview] = useState<{ key: string; refId: number; name: string; loading?: boolean; blob?: Blob; html?: string; truncated?: boolean; error?: string }>()
  const buying = useRef(false), favoriting = useRef(false)
  const wallet = useScopedData<{ canPay?: boolean; demoEnabled?: boolean }>('/wallet')
  const purchaseEnabled = !user || (!!wallet.data && wallet.data.canPay !== false && wallet.data.demoEnabled !== false)
  useEffect(() => {
    setLoaded(undefined); setError(''); setBusy(false); setFavoriteBusy(false); setResourcePreview(undefined); buying.current = false; favoriting.current = false
    if (loading) return
    const request = scope.begin()
    void api<Toolkit>(`/toolkits/${id}`, { signal: request.signal }).then(item => { if (request.current()) setLoaded({ key: scope.key, item }) }).catch(e => { if (request.current()) setError(e.message) })
  }, [id, scope, loading, retry])
  useEffect(() => {
    if (resourcePreview?.key !== scope.key) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') { scope.begin('resource-preview'); setResourcePreview(undefined) } }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [resourcePreview?.key, scope])
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
  const download = async (path: string, filename: string) => {
    if (!user) return openAuth()
    const request = scope.begin('download')
    try { await downloadAuthed(path, filename, request) }
    catch (e) { if (request.current()) toast.error((e as Error).message) }
  }
  const resources = item.resources?.length ? item.resources : item.resourceName ? [{ refId: 0, name: item.resourceName, size: 0, locked: !downloadable, downloadUrl: `/toolkits/${item.id}/download` }] : []
  const visibleResourcePreview = resourcePreview?.key === scope.key ? resourcePreview : undefined
  const closeResourcePreview = () => { scope.begin('resource-preview'); setResourcePreview(undefined) }
  const openResourcePreview = async (resource: ToolkitResource) => {
    if (!user) return openAuth()
    if (resource.locked || !resource.refId) return
    const request = scope.begin('resource-preview')
    setResourcePreview({ key: scope.key, refId: resource.refId, name: resource.name, loading: true })
    try {
      const { blob, contentType } = await fetchAuthedBlob(`/toolkits/${item.id}/resources/${resource.refId}/preview`, request)
      if (!request.current()) return
      if (contentType === 'text/html') {
        // Even the converted, server-sanitized HTML passes through DocumentRenderer's client allowlist.
        const html = await blob.slice(0, 2 * 1024 * 1024).text()
        if (request.current()) setResourcePreview({ key: scope.key, refId: resource.refId, name: resource.name, html, truncated: blob.size > 2 * 1024 * 1024 })
      } else if (['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 'text/plain'].includes(contentType)) {
        setResourcePreview({ key: scope.key, refId: resource.refId, name: resource.name, blob })
      } else throw new Error('此文件类型暂不支持在线预览，请下载后查看')
    } catch (e) {
      if (request.current()) setResourcePreview({ key: scope.key, refId: resource.refId, name: resource.name, error: (e as Error).message })
    }
  }
  const rate = async (score: number) => {
    if (!user) return openAuth()
    if (scoreBusy) return
    setScoreBusy(true); const request = scope.begin('rate')
    try {
      const result = await api<{ rating: number; ratingCount: number }>(`/toolkits/${item.id}/rating`, { method: 'PUT', body: JSON.stringify({ score }), signal: request.signal })
      if (request.current()) { setMyScore(score); setLoaded(prev => prev && { ...prev, item: { ...prev.item, rating: result.rating, ratingCount: result.ratingCount } }); toast.success('评分已提交，感谢反馈') }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) setScoreBusy(false) }
  }
  return <div className="pb-12">
    <div className="bg-ink text-white"><div className="mx-auto max-w-[1240px] px-4 py-6 sm:px-6 lg:px-8"><Link to="/toolkits" className="inline-flex items-center gap-2 text-sm text-white/50 hover:text-white"><ArrowLeft size={16} />返回工具库</Link><div className="mt-7 grid gap-9 pb-8 lg:grid-cols-[1.1fr_.9fr] lg:items-center">
      <div><div className="flex flex-wrap gap-2"><span className="chip bg-mint text-ink">{item.category}</span><span className="chip bg-white/10 text-white/70">{item.level}</span>{item.featured && <span className="chip bg-amber-400/15 text-amber-300">精选工具包</span>}</div><h1 className="mt-5 text-3xl font-black leading-tight sm:text-5xl">{item.title}</h1><p className="mt-5 max-w-2xl text-base leading-8 text-white/60">{item.summary}</p><div className="mt-6 flex flex-wrap items-center gap-5 text-sm text-white/50"><span className="font-semibold text-white">{item.author.nickname}</span><span><Star size={15} className="mr-1 inline fill-amber-400 text-amber-400" />{item.rating}</span><span><Download size={15} className="mr-1 inline" />{item.downloads} 次下载</span><span><BookOpen size={15} className="mr-1 inline" />{item.lessonCount} 节教程</span></div></div>
      <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/5 p-2"><ProtectedImage src={item.cover} protectedCover={item.status !== 'published'} className="aspect-video w-full rounded-[1.15rem] object-cover opacity-80" alt="" /><div className="absolute inset-2 bg-gradient-to-t from-ink/60 to-transparent" /><button disabled={busy || (!unlocked && !preview && !purchaseEnabled)} aria-label={unlocked ? '开始学习工具包' : preview ? '试看教程' : '购买工具包'} onClick={() => preview && !unlocked ? navigate(`/toolkits/${item.id}/learn?lesson=${preview.id}`) : start()} className="absolute inset-0 m-auto grid size-16 place-items-center rounded-full bg-mint text-ink shadow-glow transition hover:scale-105"><PlayCircle size={28} /></button></div>
    </div></div></div>
    <div className="mx-auto grid max-w-[1240px] gap-7 px-4 py-8 sm:px-6 lg:grid-cols-[1fr_350px] lg:px-8">
      <div className="space-y-7"><section className="panel p-6 sm:p-8"><h2 className="text-xl font-black text-ink">工具包介绍</h2><div className="mt-4 whitespace-pre-line text-sm leading-8 text-slate-600">{item.description}</div><div className="mt-6 flex flex-wrap gap-2">{item.tags.map(x => <span key={x} className="chip bg-slate-100 text-slate-500">#{x}</span>)}</div></section>
        <section className="panel p-6 sm:p-8"><div className="flex items-center justify-between"><h2 className="text-xl font-black text-ink">学习目录</h2><span className="text-xs text-slate-400">约 {item.lessons?.reduce((a, x) => a + x.duration, 0) ?? 0} 分钟</span></div><div className="mt-5 divide-y">{item.lessons?.map((lesson, index) => <div key={lesson.id} className="flex items-center gap-4 py-4"><span className={cn('grid size-9 shrink-0 place-items-center rounded-xl text-xs font-black', !lesson.locked ? 'bg-mint text-ink' : 'bg-slate-100 text-slate-400')}>{String(index + 1).padStart(2, '0')}</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-slate-700">{lesson.title}</p><p className="mt-1 text-xs text-slate-400"><Clock3 size={12} className="mr-1 inline" />{lesson.duration} 分钟 {lesson.isPreview && <span className="ml-2 text-emerald-700">可试看</span>}</p></div>{lesson.locked ? <LockKeyhole size={16} className="text-slate-300" /> : <Link className="btn-ghost text-xs" to={`/toolkits/${item.id}/learn?lesson=${lesson.id}`}>{lesson.isPreview && !unlocked ? '试看' : '学习'}<CheckCircle2 size={17} className="text-emerald-600" /></Link>}</div>)}</div></section>
        <section className="panel p-6 sm:p-8"><h2 className="font-black text-ink">配套资源</h2><p className="mt-1 text-xs text-slate-400">脚本仅作附件下载，不会在平台执行</p>
          <div className="mt-4 space-y-2">{resources.length ? resources.map((x, i) => <div key={x.refId || i} className="flex items-center gap-3 rounded-2xl border bg-slate-50/60 px-4 py-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-emerald-100 text-emerald-700"><FileArchive size={16} /></span>
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-ink">{x.name}</p>{x.size > 0 && <p className="mt-0.5 text-xs text-slate-400">{(x.size / 1024 / 1024).toFixed(2)} MB</p>}</div>
            {x.locked ? <span className="chip bg-slate-100 text-slate-400"><LockKeyhole size={12} />购买后可下载</span> : <div className="flex shrink-0 gap-1">{x.refId > 0 && <button type="button" className="btn-ghost text-xs" onClick={() => void openResourcePreview(x)}><Eye size={14} />预览</button>}<button type="button" className="btn-ghost text-xs" onClick={() => void download(x.downloadUrl || `/toolkits/${item.id}/download`, x.name)}><Download size={14} />下载</button></div>}
          </div>) : <p className="rounded-2xl bg-slate-50 px-4 py-6 text-center text-sm text-slate-400">作者尚未上传可下载文件</p>}</div>
        </section>
        <section className="panel p-6 sm:p-8"><h2 className="font-black text-ink">工具包评分</h2>
          <div className="mt-3 flex items-center gap-3">
            <p className="text-3xl font-black text-ink">{Number(item.rating).toFixed(1)}</p>
            <div>{[1, 2, 3, 4, 5].map(n => <Star key={n} size={16} className={n <= Math.round(item.rating) ? 'inline fill-amber-400 text-amber-400' : 'inline text-slate-300'} />)}
              <p className="mt-1 text-xs text-slate-400">{item.ratingCount ? `${item.ratingCount} 人评分` : '购买后即可评分'}</p></div>
            {item.purchased && <div className="ml-auto flex items-center gap-1">{[1, 2, 3, 4, 5].map(n => <button key={n} disabled={scoreBusy} aria-label={`评 ${n} 分`} onClick={() => void rate(n)} className="p-1"><Star size={20} className={n <= myScore ? 'fill-pine text-pine' : 'text-slate-300 hover:text-amber-400'} /></button>)}</div>}
          </div>
        </section>
      </div>
      <aside><div className="panel sticky top-24 p-6"><p className="text-xs font-bold text-slate-400">完整工具包</p><div className="mt-2 text-3xl font-black text-ink">{money(item.price)}</div>{!unlocked && <p className="mt-2 text-xs text-slate-400">购买后永久解锁完整教程和配套资源</p>}{!unlocked && wallet.data && !purchaseEnabled && <p role="alert" className="mt-4 text-sm text-amber-800">当前未开放模拟交易，尚未接入真实支付；可试看已开放教程。</p>}{!unlocked && wallet.error && <p role="alert" className="mt-4 text-sm text-red-600">购买能力读取失败<button className="btn-ghost" onClick={wallet.reload}>重试</button></p>}<button disabled={busy || (!unlocked && !purchaseEnabled)} onClick={start} className="btn-mint mt-6 w-full py-3.5">{unlocked ? '开始学习' : busy ? '正在创建订单…' : '立即购买'} <ArrowRight size={17} /></button>{preview && !unlocked && <Link className="btn-primary mt-3 w-full" to={`/toolkits/${item.id}/learn?lesson=${preview.id}`}><PlayCircle size={16} />免费试看</Link>}<button disabled={favoriteBusy} onClick={favorite} className="btn-ghost mt-3 w-full"><Bookmark size={16} className={item.favorited ? 'fill-pine text-pine' : ''} />{item.favorited ? '已收藏' : '收藏工具包'}</button><div className="mt-6 space-y-3 border-t pt-5 text-xs text-slate-500"><p><ShieldCheck size={15} className="mr-2 inline text-emerald-600" />钱包余额结算，平台暂不支持退款</p><p><BookOpen size={15} className="mr-2 inline text-emerald-600" />已购内容下架后仍可学习</p><p><Download size={15} className="mr-2 inline text-emerald-600" />{unlocked ? '配套资源一键下载' : '购买后解锁配套资源下载'}</p></div></div></aside>
    </div>
    {visibleResourcePreview && <div className="fixed inset-0 z-50 grid place-items-center bg-ink/75 p-4" onMouseDown={event => { if (event.target === event.currentTarget) closeResourcePreview() }}>
      <section role="dialog" aria-modal="true" aria-label={`预览资源：${visibleResourcePreview.name}`} className="max-h-[90vh] w-full max-w-4xl overflow-auto rounded-2xl bg-white p-5 shadow-xl sm:p-7">
        <div className="mb-4 flex items-start justify-between gap-4 border-b pb-4"><div><h2 className="break-all text-lg font-black text-ink">{visibleResourcePreview.name}</h2><p className="mt-1 text-xs text-slate-500">仅展示有权访问的资源，不会运行脚本或外部文档代码。</p></div><button type="button" autoFocus className="btn-ghost shrink-0" onClick={closeResourcePreview}>关闭预览</button></div>
        {visibleResourcePreview.loading ? <p role="status">正在载入资源预览…</p> : visibleResourcePreview.error ? <p role="alert" className="text-sm text-rose-700">{visibleResourcePreview.error}</p> : visibleResourcePreview.html !== undefined ? <div><DocumentRenderer contentType="doc" convertedHtml={visibleResourcePreview.html} />{visibleResourcePreview.truncated && <p className="mt-3 text-xs text-slate-500">仅展示前 2 MiB 转换内容；下载原件查看全文。</p>}</div> : visibleResourcePreview.blob ? <SelectedFilePreview file={visibleResourcePreview.blob} name={visibleResourcePreview.name} /> : null}
      </section>
    </div>}
  </div>
}
