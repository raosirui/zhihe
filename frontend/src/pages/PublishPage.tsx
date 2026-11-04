import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Image, PackageOpen, Plus, Trash2, UploadCloud } from 'lucide-react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../lib'
import { useAuth } from '../AuthContext'
import { useRequestScope } from '../useRequestScope'

type DraftLesson = { title: string; content: string; duration: number; image: string; contentType?: string }
export function PublishPage() {
  const { user, loading, restoreError, refresh } = useAuth(); const navigate = useNavigate(), { openAuth } = useOutletContext<{ openAuth: () => void }>(), scope = useRequestScope('publish')
  const [busy, setBusy] = useState<'published' | 'draft' | ''>(''); const [resources, setResources] = useState<File[]>([]); const inFlight = useRef(false)
  const [form, setForm] = useState({ title: '', summary: '', description: '', category: '生信分析', level: '入门', price: '0', tags: '', cover: '' })
  const [lessons, setLessons] = useState<DraftLesson[]>([{ title: '认识工作流与环境准备', content: '', duration: 10, image: '' }])
  const [lessonFiles, setLessonFiles] = useState<(File | null)[]>([null])
  useEffect(() => {
    setBusy(''); inFlight.current = false; setResources([])
    setForm({ title: '', summary: '', description: '', category: '生信分析', level: '入门', price: '0', tags: '', cover: '' })
    setLessons([{ title: '认识工作流与环境准备', content: '', duration: 10, image: '' }])
    setLessonFiles([null])
  }, [scope.key])
  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <div className="p-12 text-center"><p role="alert">{restoreError}</p><button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><PackageOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 text-xl font-black">登录后即可发布工具包</h1><button onClick={openAuth} className="btn-primary mt-6">登录 / 注册</button></div>
  const submit = async (e: React.FormEvent, status: 'published' | 'draft') => {
    e.preventDefault(); if (inFlight.current) return
    inFlight.current = true; setBusy(status); const request = scope.begin('publish')
    try {
      const body = new FormData(); Object.entries(form).forEach(([k, v]) => body.append(k, v)); body.append('status', status)
      body.append('lessons', JSON.stringify(lessons.map((lesson, i) => ({ ...lesson, preview: i === 0 }))))
      lessonFiles.forEach((file, i) => { if (file) body.append('lesson_file_' + i, file) })
      resources.forEach(file => body.append('resource', file))
      const result = await api<{ id: number }>('/toolkits', { method: 'POST', body, signal: request.signal })
      if (request.current()) { toast.success(status === 'draft' ? '已保存草稿，可在我的工具包中发布' : '工具包发布成功'); navigate(`/toolkits/${result.id}`) }
    } catch (e) { if (request.current()) toast.error((e as Error).message) }
    finally { if (request.current()) { inFlight.current = false; setBusy('') } }
  }
  const updateLesson = (index: number, key: keyof DraftLesson, value: string | number) => setLessons(lessons.map((x, i) => i === index ? { ...x, [key]: value } : x))
  const updateLessonFile = (index: number, file: File | null) => setLessonFiles(lessonFiles.map((x, i) => i === index ? file : x))
  const importMarkdown = (index: number, file?: File) => {
    if (!file) return
    if (!/\.(md|markdown|txt)$/i.test(file.name)) return toast.error('仅支持 .md / .markdown / .txt 文件')
    if (file.size > 1024 * 1024) return toast.error('正文文件不能超过 1MB')
    void file.text().then(text => { updateLesson(index, 'content', text); toast.success(`已导入 ${file.name}`) }).catch(() => toast.error('文件读取失败'))
  }
  return <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:py-12"><Link to="/me/toolkits" className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回我的工具包</Link><div className="mt-5 flex flex-wrap items-end justify-between gap-3"><div><p className="text-sm font-bold text-emerald-700">CREATE</p><h1 className="mt-1 text-3xl font-black text-ink">发布科研工具包</h1><p className="mt-2 text-sm text-slate-500">清晰描述你的方法，并把每一步写成可复现的教程。</p></div><Link to="/publish/batch" className="btn-ghost">多个工具包？批量上传</Link></div><form onSubmit={e => void submit(e, 'published')} className="mt-8 space-y-6">
    <section className="panel p-6 sm:p-8"><h2 className="text-lg font-black text-ink">基本信息</h2><div className="mt-5 grid gap-5 sm:grid-cols-2"><div className="sm:col-span-2"><label className="label">工具包名称 *</label><input required className="input" placeholder="例如：单细胞 RNA-seq 标准分析工作流" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></div><div className="sm:col-span-2"><label className="label">一句话简介 *</label><input required className="input" maxLength={500} placeholder="告诉学习者能解决什么问题" value={form.summary} onChange={e => setForm({ ...form, summary: e.target.value })} /></div><div className="sm:col-span-2"><label className="label">详细介绍 *</label><textarea required className="input min-h-32 resize-y" placeholder="适用场景、学习目标、运行环境和前置知识…" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></div><div><label className="label">分类</label><select className="input" value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>{['生信分析', '单细胞', '空间组学', '结构生物学', '基因编辑'].map(x => <option key={x}>{x}</option>)}</select></div><div><label className="label">难度</label><select className="input" value={form.level} onChange={e => setForm({ ...form, level: e.target.value })}>{['入门', '进阶', '专业'].map(x => <option key={x}>{x}</option>)}</select></div><div><label className="label">定价（元）</label><input type="number" min="0" max="9999" step="0.01" className="input" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} /><p className="mt-2 text-xs text-slate-400">设为 0 即免费；付费订单作者获得 20% 分成</p></div><div><label className="label">标签</label><input className="input" placeholder="用英文逗号分隔" value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })} /></div><div className="sm:col-span-2"><label className="label"><Image size={15} className="mr-1.5 inline" />封面图片 URL</label><input className="input" placeholder="https://..." value={form.cover} onChange={e => setForm({ ...form, cover: e.target.value })} /></div></div></section>
    <section className="panel p-6 sm:p-8"><div className="flex items-center justify-between"><div><h2 className="text-lg font-black text-ink">图文教程步骤</h2><p className="mt-1 text-xs text-slate-400">第一步会作为试看内容开放</p></div><button type="button" className="btn-ghost" onClick={() => { setLessons([...lessons, { title: '', content: '', duration: 10, image: '' }]); setLessonFiles([...lessonFiles, null]) }}><Plus size={16} />添加步骤</button></div><div className="mt-6 space-y-5">{lessons.map((lesson, i) => <div key={i} className="rounded-2xl border bg-slate-50/50 p-4 sm:p-5"><div className="flex items-center justify-between"><span className="chip bg-ink text-white">步骤 {i + 1}{i === 0 && ' · 试看'}</span>{lessons.length > 1 && <button type="button" onClick={() => { setLessons(lessons.filter((_, j) => j !== i)); setLessonFiles(lessonFiles.filter((_, j) => j !== i)) }} className="text-red-400"><Trash2 size={17} /></button>}</div><div className="mt-4 grid gap-4 sm:grid-cols-[1fr_130px]"><input required className="input" placeholder="步骤标题" value={lesson.title} onChange={e => updateLesson(i, 'title', e.target.value)} /><input required type="number" min="1" className="input" placeholder="分钟" value={lesson.duration} onChange={e => updateLesson(i, 'duration', Number(e.target.value))} /></div><textarea required className="input mt-4 min-h-36 resize-y" placeholder="详细操作步骤、命令、参数与注意事项…（也可点击下方按钮导入 Markdown 文件）" value={lesson.content} onChange={e => updateLesson(i, 'content', e.target.value)} /><div className="mt-2 flex flex-wrap items-center gap-3"><label className="btn-ghost text-xs"><UploadCloud size={14} />导入 .md 文件<input type="file" accept=".md,.markdown,.txt,text/markdown" className="hidden" onChange={e => importMarkdown(i, e.target.files?.[0])} /></label><label className="btn-ghost text-xs"><UploadCloud size={14} />上传文档<input type="file" accept=".md,.markdown,.txt,.docx,.pdf,.html,.htm,.rtf,.pptx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (!f) return; updateLessonFile(i, f); toast.success(`已选择文档：${f.name}`) } } /></label>{lessonFiles[i] && <span className="text-xs text-emerald-600">📎 {lessonFiles[i]?.name}</span>}<span className="text-xs text-slate-400">支持 Markdown / Word / PDF / HTML / PPT</span></div><input className="input mt-4" placeholder="本步骤配图 URL（可选）" value={lesson.image} onChange={e => updateLesson(i, 'image', e.target.value)} /></div>)}</div></section>
    <section className="panel p-6 sm:p-8"><h2 className="text-lg font-black text-ink">配套资源</h2><p className="mt-1 text-xs text-slate-400">可上传多个文件（压缩包、脚本、示例数据），每个最大 40MB，最多 20 个</p><label className="mt-5 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed p-8 text-center transition hover:border-pine hover:bg-emerald-50/30"><UploadCloud className="text-emerald-700" /><b className="mt-3 text-sm text-ink">{resources.length ? `已选择 ${resources.length} 个文件` : '点击选择资源文件（可多选）'}</b><span className="mt-1 text-xs text-slate-400">{resources.map((x, i) => <span key={i}>{x.name}{i < resources.length - 1 ? ' · ' : ''}</span>)}</span><input type="file" multiple className="hidden" onChange={e => setResources(Array.from(e.target.files ?? []).slice(0, 20))} /></label>{resources.length > 0 && <button type="button" className="btn-ghost mt-3" onClick={() => setResources([])}><Trash2 size={14} />清空已选文件</button>}</section>
    <div className="flex justify-end gap-3"><Link to="/me/toolkits" className="btn-ghost">取消</Link><button type="button" disabled={!!busy} onClick={e => void submit(e, 'draft')} className="btn-primary">{busy === 'draft' ? '正在保存…' : '保存草稿'}</button><button disabled={!!busy} className="btn-mint px-7">{busy === 'published' ? '正在发布…' : '发布工具包'}</button></div>
  </form></div>
}
