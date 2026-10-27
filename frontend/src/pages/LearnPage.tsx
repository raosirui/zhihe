import { useEffect, useState } from 'react'
import { ArrowLeft, Check, CheckCircle2, ChevronLeft, ChevronRight, Clock3, List, LockKeyhole } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import { toast } from 'sonner'
import { useAuth } from '../AuthContext'
import { api, cn } from '../lib'
import type { Toolkit } from '../types'

export function LearnPage() {
  const { id } = useParams(); const navigate = useNavigate(); const { user } = useAuth()
  const [item, setItem] = useState<Toolkit>(); const [current, setCurrent] = useState(0); const [menu, setMenu] = useState(false)
  const [done, setDone] = useState<number[]>([])
  useEffect(() => {
    api<Toolkit>(`/toolkits/${id}`).then(x => { setItem(x); const first = x.lessons?.findIndex(y => !y.locked) ?? 0; setCurrent(Math.max(first, 0)) })
      .catch(e => { toast.error(e.message); navigate(`/toolkits/${id}`) })
  }, [id])
  useEffect(() => { if (user && id) api<number[]>(`/me/lesson-progress?toolkit_id=${id}`).then(setDone).catch(() => {}) }, [user, id])
  if (!item?.lessons) return <div className="p-10 text-center text-slate-400">正在加载学习空间…</div>
  const lessons = item.lessons
  const lesson = lessons[current]
  const complete = async () => {
    if (!user) return toast.error('请先登录后记录学习进度')
    try {
      await api(`/lessons/${lesson.id}/complete`, { method: 'POST' })
      setDone(prev => prev.includes(lesson.id) ? prev : [...prev, lesson.id])
      toast.success('本节已完成')
      if (current < lessons.length - 1 && !lessons[current + 1].locked) setCurrent(current + 1)
    } catch (e) { toast.error((e as Error).message) }
  }
  return <div className="min-h-[calc(100vh-64px)] bg-[#f2f4ef]"><div className="border-b bg-white"><div className="mx-auto flex h-14 max-w-[1440px] items-center justify-between px-4"><Link to={`/toolkits/${item.id}`} className="flex items-center gap-2 text-sm font-semibold text-slate-500"><ArrowLeft size={17} />退出学习</Link><p className="hidden max-w-xl truncate text-sm font-bold text-ink sm:block">{item.title}</p><button className="btn-ghost px-3 py-2 lg:hidden" onClick={() => setMenu(!menu)}><List size={17} /></button></div></div>
    <div className="mx-auto grid max-w-[1440px] lg:grid-cols-[300px_1fr]"><aside className={cn('border-r bg-white lg:block', menu ? 'block' : 'hidden')}><div className="sticky top-[120px] max-h-[calc(100vh-120px)] overflow-y-auto p-5"><p className="mb-4 text-xs font-black uppercase tracking-widest text-slate-400">课程目录</p><div className="space-y-2">{item.lessons.map((x, i) => <button disabled={x.locked} onClick={() => { setCurrent(i); setMenu(false) }} key={x.id} className={cn('flex w-full items-start gap-3 rounded-2xl p-3 text-left transition', i === current ? 'bg-ink text-white' : x.locked ? 'cursor-not-allowed text-slate-300' : 'text-slate-600 hover:bg-slate-50')}><span className={cn('grid size-7 shrink-0 place-items-center rounded-lg text-[10px] font-black', i === current ? 'bg-mint text-ink' : 'bg-slate-100 text-slate-500')}>{x.locked ? <LockKeyhole size={12} /> : done.includes(x.id) ? <Check size={13} /> : i + 1}</span><span><b className="line-clamp-2 text-sm">{x.title}</b><small className="mt-1 flex items-center gap-1 opacity-50"><Clock3 size={11} />{x.duration} 分钟</small></span></button>)}</div></div></aside>
      <main className="min-w-0 px-4 py-8 sm:px-8 lg:px-12 lg:py-12"><article className="mx-auto max-w-4xl rounded-3xl bg-white p-6 shadow-sm sm:p-10"><div className="flex items-center gap-3 text-xs font-bold text-emerald-700"><span>第 {current + 1} 节</span><span>·</span><span>{lesson.duration} 分钟</span>{done.includes(lesson.id) && <span className="ml-auto flex items-center gap-1 text-slate-400"><CheckCircle2 size={14} />已完成</span>}</div><h1 className="mt-3 text-2xl font-black text-ink sm:text-3xl">{lesson.title}</h1>{lesson.image && <img src={lesson.image} alt="" className="mt-7 aspect-[16/7] w-full rounded-2xl object-cover" />}<div className="markdown mt-8"><ReactMarkdown rehypePlugins={[rehypeSanitize]}>{lesson.content || ''}</ReactMarkdown></div><div className="mt-10 flex items-center justify-between border-t pt-6"><button disabled={current === 0} onClick={() => setCurrent(current - 1)} className="btn-ghost"><ChevronLeft size={16} />上一节</button>{current < item.lessons.length - 1 && !item.lessons[current + 1].locked ? <button onClick={() => setCurrent(current + 1)} className="btn-primary">下一节<ChevronRight size={16} /></button> : <button onClick={complete} className="btn-mint"><Check size={16} />{done.includes(lesson.id) ? '本节已完成' : '本节完成'}</button>}</div></article></main>
    </div></div>
}
