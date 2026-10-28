import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, List, LockKeyhole } from 'lucide-react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, cn } from '../lib'
import type { Toolkit } from '../types'
import { useRequestScope } from '../useRequestScope'
import { ProtectedImage } from '../components/ProtectedImage'
import { Empty } from '../components/Empty'

export function LearnPage() {
  const { id } = useParams(); const { user, loading, restoreError, refresh } = useAuth()
  const [params] = useSearchParams(), requestedLesson = Number(params.get('lesson'))
  const scope = useRequestScope(`${id}:${requestedLesson}`)
  const [loaded, setLoaded] = useState<{ key: string; item: Toolkit }>()
  const [current, setCurrent] = useState(0); const [menu, setMenu] = useState(false)
  const [done, setDone] = useState<number[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  const selection = useRef(0), inFlight = useRef(false)
  const select = (index: number) => { selection.current++; setCurrent(index); setMenu(false) }
  useEffect(() => {
    setLoaded(undefined); setDone([]); setCurrent(0); setError(''); setBusy(false); inFlight.current = false; selection.current++
    if (loading || restoreError) return
    const request = scope.begin()
    void api<Toolkit>(`/toolkits/${id}`, { signal: request.signal }).then(item => {
      if (!request.current()) return
      setLoaded({ key: scope.key, item })
      const requested = item.lessons?.findIndex(x => x.id === requestedLesson && !x.locked) ?? -1
      const first = requested >= 0 ? requested : item.lessons?.findIndex(x => !x.locked) ?? 0
      select(Math.max(first, 0))
    }).catch(e => { if (request.current()) setError(e.message) })
  }, [id, scope, loading, restoreError, retry])
  useEffect(() => {
    setDone([])
    if (!user || loading || restoreError) return
    const request = scope.begin('progress')
    void api<number[]>(`/me/lesson-progress?toolkit_id=${id}`, { signal: request.signal }).then(x => {
      // An initial read may predate a successful completion POST in this scope.
      // Preserve confirmed completions while also retaining historic progress.
      if (request.current()) setDone(prev => [...new Set([...prev, ...x])])
    }).catch(e => { if (request.current()) toast.error(`进度读取失败：${e.message}`) })
  }, [id, scope, user?.id, loading, restoreError, retry])
  if (restoreError) return <Empty title="会话恢复失败" detail={restoreError} action={<button onClick={() => void refresh().catch(() => {})} className="btn-primary">重试恢复</button>} />
  if (error) return <Empty title="课程读取失败" detail={error} action={<button className="btn-primary" onClick={() => setRetry(x => x + 1)}>重试</button>} />
  const item = loaded?.key === scope.key ? loaded.item : undefined
  if (!item) return <div className="p-10 text-center text-slate-400">正在加载学习空间…</div>
  const lessons = item.lessons ?? []
  if (!lessons.length) return <Empty title="暂无课程" detail="作者尚未添加课程内容。" action={<Link className="btn-primary" to={`/toolkits/${id}`}>返回工具包</Link>} />
  const lesson = lessons[current]
  if (!lesson || lesson.locked) return <Empty title="课程尚未解锁" action={<Link className="btn-primary" to={`/toolkits/${id}`}>返回工具包</Link>} />
  const complete = async (advance = false) => {
    if (inFlight.current) return
    // Anonymous learning may advance, but must never claim persisted completion.
    if (!user) { if (advance) select(current + 1); else toast.info('登录后可保存学习进度'); return }
    inFlight.current = true; setBusy(true)
    const request = scope.begin('complete'), selected = selection.current
    try {
      await api(`/lessons/${lesson.id}/complete`, { method: 'POST', signal: request.signal })
      if (!request.current()) return
      setDone(prev => prev.includes(lesson.id) ? prev : [...prev, lesson.id])
      toast.success('本节已完成')
      if (advance && selected === selection.current && current < lessons.length - 1 && !lessons[current + 1].locked) select(current + 1)
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { inFlight.current = false; setBusy(false) } }
  }
  return <div className="min-h-[calc(100vh-64px)] bg-[#f2f4ef]"><div className="border-b bg-white"><div className="mx-auto flex h-14 max-w-[1440px] items-center justify-between px-4"><Link to={`/toolkits/${item.id}`} className="flex items-center gap-2 text-sm font-semibold text-slate-500"><ArrowLeft size={17} />退出学习</Link><p className="hidden max-w-xl truncate text-sm font-bold text-ink sm:block">{item.title}</p><button className="btn-ghost px-3 py-2 lg:hidden" onClick={() => setMenu(!menu)}><List size={17} /></button></div></div>
    <div className="mx-auto grid max-w-[1440px] lg:grid-cols-[300px_1fr]"><aside className={cn('border-r bg-white lg:block', menu ? 'block' : 'hidden')}><div className="sticky top-[120px] max-h-[calc(100vh-120px)] overflow-y-auto p-5"><p className="mb-4 text-xs font-black uppercase tracking-widest text-slate-400">课程目录</p><div className="space-y-2">{lessons.map((x, i) => <button disabled={x.locked} onClick={() => select(i)} key={x.id} className={cn('flex w-full items-start gap-3 rounded-2xl p-3 text-left transition', i === current ? 'bg-ink text-white' : x.locked ? 'cursor-not-allowed text-slate-300' : 'text-slate-600 hover:bg-slate-50')}><span className={cn('grid size-7 shrink-0 place-items-center rounded-lg text-[10px] font-black', i === current ? 'bg-mint text-ink' : 'bg-slate-100 text-slate-500')}>{x.locked ? <LockKeyhole size={12} /> : done.includes(x.id) ? <Check size={13} /> : i + 1}</span><span><b className="line-clamp-2 text-sm">{x.title}</b><small className="mt-1 flex items-center gap-1 opacity-50"><Clock3 size={11} />{x.duration} 分钟</small></span></button>)}</div></div></aside>
      <main className="min-w-0 px-4 py-8 sm:px-8 lg:px-12 lg:py-12"><article className="mx-auto max-w-4xl rounded-3xl bg-white p-6 shadow-sm sm:p-10"><div className="flex items-center gap-3 text-xs font-bold text-emerald-700"><span>第 {current + 1} 节</span><span>·</span><span>{lesson.duration} 分钟</span>{done.includes(lesson.id) && <span className="ml-auto flex items-center gap-1 text-slate-400"><CheckCircle2 size={14} />已完成</span>}</div><h1 className="mt-3 text-2xl font-black text-ink sm:text-3xl">{lesson.title}</h1>{lesson.image && <ProtectedImage src={lesson.image} alt="" className="mt-7 aspect-[16/7] w-full rounded-2xl object-cover" />}<div className="markdown mt-8"><ReactMarkdown rehypePlugins={[rehypeSanitize]} components={{ img: ({ node: _node, ...props }) => <ProtectedImage {...props} /> }}>{lesson.content || ''}</ReactMarkdown></div><div className="mt-10 flex items-center justify-between border-t pt-6"><button disabled={current === 0 || lessons[current - 1].locked} onClick={() => select(current - 1)} className="btn-ghost"><ChevronLeft size={16} />上一节</button><div className="flex gap-2"><button disabled={busy} onClick={() => void complete()} className="btn-mint"><Check size={16} />{busy ? '保存中…' : done.includes(lesson.id) ? '本节已完成' : '本节完成'}</button>{current < lessons.length - 1 && !lessons[current + 1].locked && <button disabled={busy} onClick={() => void complete(true)} className="btn-primary">下一节<ChevronRight size={16} /></button>}</div></div></article></main>
    </div></div>
}
