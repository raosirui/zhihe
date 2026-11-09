import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Image, PackageOpen, Plus, Trash2, UploadCloud } from 'lucide-react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../../shared/api'
import { useAuth } from '../../AuthContext'
import { useRequestScope } from '../../useRequestScope'
import { SelectedFilePreview, SelectedImage } from '../../components/SelectedFilePreview'

type DraftLesson = { title: string; content: string; duration: number; image: string; contentType?: string }
type PublishStatus = 'published' | 'draft'

const MAX_TITLE_LENGTH = 120
const MAX_DESCRIPTION_LENGTH = 20_000
const MAX_TAGS_LENGTH = 300
const MAX_LESSON_TITLE_LENGTH = 160
const MAX_LESSONS = 50
const MAX_FILE_SIZE = 40 * 1024 * 1024
const MAX_REQUEST_SIZE = 100 * 1024 * 1024

function uploadError(coverFile: File | null, lessonImages: (File | null)[], lessonFiles: (File | null)[], resources: File[]) {
  const files = [coverFile, ...lessonImages, ...lessonFiles, ...resources].filter((file): file is File => file !== null)
  const oversized = files.find(file => file.size > MAX_FILE_SIZE)
  if (oversized) return `${oversized.name} 超过单个文件 40 MB 的限制`
  if (files.reduce((total, file) => total + file.size, 0) > MAX_REQUEST_SIZE) {
    return '封面、步骤配图、教程原件与资源的文件总大小不能超过 100 MB'
  }
  return ''
}

function imageError(file: File) {
  if (!/\.(?:jpe?g|png|webp|gif)$/i.test(file.name) || (file.type && !['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type))) {
    return '图片仅支持 JPG、PNG、WebP、GIF 文件（不支持 SVG）'
  }
  return ''
}

export function PublishPage() {
  const { user, loading, restoreError, refresh } = useAuth()
  const navigate = useNavigate()
  const { openAuth } = useOutletContext<{ openAuth: () => void }>()
  const scope = useRequestScope('publish')
  const [busy, setBusy] = useState<PublishStatus | ''>('')
  const [resources, setResources] = useState<File[]>([])
  const [resourceError, setResourceError] = useState('')
  const [formError, setFormError] = useState('')
  const [lessonFileErrors, setLessonFileErrors] = useState<(string | null)[]>([null])
  const [coverFile, setCoverFile] = useState<File | null>(null)
  const [coverFileError, setCoverFileError] = useState('')
  const [lessonImages, setLessonImages] = useState<(File | null)[]>([null])
  const [lessonImageErrors, setLessonImageErrors] = useState<(string | null)[]>([null])
  const inFlight = useRef(false)
  const [form, setForm] = useState({ title: '', description: '', category: '生信分析', level: '入门', price: '0', tags: '', cover: '' })
  const [lessons, setLessons] = useState<DraftLesson[]>([{ title: '认识工作流与环境准备', content: '', duration: 10, image: '' }])
  const [lessonFiles, setLessonFiles] = useState<(File | null)[]>([null])

  useEffect(() => {
    setBusy('')
    inFlight.current = false
    setResources([])
    setResourceError('')
    setFormError('')
    setLessonFileErrors([null])
    setCoverFile(null)
    setCoverFileError('')
    setLessonImages([null])
    setLessonImageErrors([null])
    setForm({ title: '', description: '', category: '生信分析', level: '入门', price: '0', tags: '', cover: '' })
    setLessons([{ title: '认识工作流与环境准备', content: '', duration: 10, image: '' }])
    setLessonFiles([null])
  }, [scope.key])

  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <div className="p-12 text-center"><p role="alert">{restoreError}</p><button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><PackageOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 text-xl font-black">登录后即可发布工具包</h1><button onClick={openAuth} className="btn-primary mt-6">登录 / 注册</button></div>

  const validate = (status: PublishStatus) => {
    const publishing = status === 'published'
    if (publishing && !form.title.trim()) return '请填写工具包名称'
    if (form.title.length > MAX_TITLE_LENGTH) return `工具包名称不能超过 ${MAX_TITLE_LENGTH} 字`
    if (publishing && !form.description.trim()) return '请填写工具包介绍'
    if (form.description.length > MAX_DESCRIPTION_LENGTH) return `工具包介绍不能超过 ${MAX_DESCRIPTION_LENGTH} 字`
    if (form.tags.length > MAX_TAGS_LENGTH) return `标签不能超过 ${MAX_TAGS_LENGTH} 字`
    if (publishing && !form.price.trim()) return '请填写工具包定价'
    if (form.price.trim() && (!Number.isFinite(Number(form.price)) || Number(form.price) < 0 || Number(form.price) > 9999)) return '定价须为 0–9999 元'
    if (form.price.trim() && !/^\d+(\.\d{1,2})?$/.test(form.price)) return '定价最多保留两位小数'
    if (lessons.length > MAX_LESSONS) return `教程步骤不能超过 ${MAX_LESSONS} 步`
    for (let i = 0; i < lessons.length; i++) {
      const lesson = lessons[i]
      if (publishing && !lesson.title.trim()) return `请填写第 ${i + 1} 步的标题`
      if (lesson.title.length > MAX_LESSON_TITLE_LENGTH) return `第 ${i + 1} 步标题不能超过 ${MAX_LESSON_TITLE_LENGTH} 字`
      if (!(status === 'draft' && lesson.duration === 0) && (!Number.isInteger(lesson.duration) || lesson.duration < 1 || lesson.duration > 600)) return `第 ${i + 1} 步的预计学习时长须为 1–600 分钟的整数`
      if (publishing && !lessonFiles[i] && !lesson.content.trim()) return `请填写第 ${i + 1} 步正文，或上传教程原件`
    }
    if (resources.length > 20) return '配套资源最多选择 20 个'
    return uploadError(coverFile, lessonImages, lessonFiles, resources)
  }

  const submit = async (status: PublishStatus) => {
    if (inFlight.current) return
    const error = validate(status)
    if (error) {
      setFormError(error)
      return
    }
    setFormError('')
    setResourceError('')
    inFlight.current = true
    setBusy(status)
    const request = scope.begin('publish')
    try {
      const body = new FormData()
      Object.entries(form).forEach(([key, value]) => body.append(key, key === 'cover' && coverFile ? '' : value))
      body.append('status', status)
      body.append('lessons', JSON.stringify(lessons.map((lesson, index) => ({ ...lesson, image: lessonImages[index] ? '' : lesson.image, preview: index === 0 }))))
      if (coverFile) body.append('cover_file', coverFile)
      lessonImages.forEach((file, index) => { if (file) body.append(`lesson_image_${index}`, file) })
      lessonFiles.forEach((file, index) => { if (file) body.append(`lesson_file_${index}`, file) })
      resources.forEach(file => body.append('resource', file))
      const result = await api<{ id: number }>('/toolkits', { method: 'POST', body, signal: request.signal })
      if (request.current()) {
        toast.success(status === 'draft' ? '已保存草稿，可在我的工具包中发布' : '工具包发布成功')
        navigate(`/toolkits/${result.id}`)
      }
    } catch (error) {
      if (request.current()) toast.error((error as Error).message)
    } finally {
      if (request.current()) {
        inFlight.current = false
        setBusy('')
      }
    }
  }

  const updateLesson = (index: number, key: keyof DraftLesson, value: string | number) => {
    setLessons(current => current.map((lesson, i) => i === index ? { ...lesson, [key]: value } : lesson))
    setFormError('')
  }

  const updateForm = (key: keyof typeof form, value: string) => {
    setForm(current => ({ ...current, [key]: value }))
    setFormError('')
  }

  const updateCoverFile = (file: File | null) => {
    const error = file ? imageError(file) || uploadError(file, lessonImages, lessonFiles, resources) : ''
    if (error) { setCoverFileError(error); return }
    setCoverFile(file)
    setCoverFileError('')
    setFormError('')
  }

  const updateLessonImage = (index: number, file: File | null) => {
    const nextImages = lessonImages.map((current, i) => i === index ? file : current)
    const error = file ? imageError(file) || uploadError(coverFile, nextImages, lessonFiles, resources) : ''
    if (error) { setLessonImageErrors(current => current.map((message, i) => i === index ? error : message)); return }
    setLessonImages(nextImages)
    setLessonImageErrors(current => current.map((message, i) => i === index ? null : message))
    setFormError('')
  }

  const updateLessonFile = (index: number, file: File | null) => {
    const nextFiles = lessonFiles.map((current, i) => i === index ? file : current)
    const error = uploadError(coverFile, lessonImages, nextFiles, resources)
    if (error) {
      setLessonFileErrors(current => current.map((message, i) => i === index ? error : message))
      return
    }
    setLessonFiles(nextFiles)
    setLessonFileErrors(current => current.map((message, i) => i === index ? null : message))
    setResourceError('')
  }

  const addResources = (selected: File[]) => {
    if (!selected.length) return
    const nextResources = [...resources, ...selected]
    if (nextResources.length > 20) {
      setResourceError('配套资源最多选择 20 个；原有已选文件已保留')
      return
    }
    const error = uploadError(coverFile, lessonImages, lessonFiles, nextResources)
    if (error) {
      setResourceError(`${error}；原有已选文件已保留`)
      return
    }
    setResources(nextResources)
    setResourceError('')
    setFormError('')
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:py-12">
      <Link to="/me/toolkits" className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回我的工具包</Link>
      <div className="mt-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm font-bold text-emerald-700">CREATE</p>
          <h1 className="mt-1 text-3xl font-black text-ink">发布科研工具包</h1>
          <p className="mt-2 text-sm text-slate-500">清晰描述你的方法，并把每一步写成可复现的教程。</p>
        </div>
        <Link to="/publish/batch" className="btn-ghost">多个工具包？批量上传</Link>
      </div>

      <form onSubmit={event => { event.preventDefault(); void submit('published') }} className="mt-8 space-y-6">
        <p className="-mb-2 text-xs text-slate-500"><span className="font-bold text-rose-600">*</span> 为必填项，其余选填。</p>
        {formError && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{formError}</p>}

        <section className="panel p-6 sm:p-8">
          <h2 className="text-lg font-black text-ink">基本信息</h2>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor="toolkit-title" className="label">工具包名称 <span aria-hidden="true" className="text-rose-600">*</span></label>
              <input id="toolkit-title" required maxLength={MAX_TITLE_LENGTH} className="input" placeholder="例如：单细胞 RNA-seq 标准分析工作流" value={form.title} onChange={event => updateForm('title', event.target.value)} />
              <p className="mt-1 text-xs text-slate-400">最多 120 字</p>
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="toolkit-description" className="label">工具包介绍 <span aria-hidden="true" className="text-rose-600">*</span></label>
              <textarea id="toolkit-description" required maxLength={MAX_DESCRIPTION_LENGTH} className="input min-h-32 resize-y" placeholder="介绍工具包的目标、适用场景、内容和运行环境…" value={form.description} onChange={event => updateForm('description', event.target.value)} />
              <p className="mt-1 text-xs text-slate-400">列表短简介自动取开头 120 字；最多 20,000 字</p>
            </div>
            <div>
              <label htmlFor="toolkit-category" className="label">分类 <span aria-hidden="true" className="text-rose-600">*</span></label>
              <select id="toolkit-category" required className="input" value={form.category} onChange={event => updateForm('category', event.target.value)}>{['生信分析', '单细胞', '空间组学', '结构生物学', '基因编辑'].map(value => <option key={value}>{value}</option>)}</select>
            </div>
            <div>
              <label htmlFor="toolkit-level" className="label">难度 <span aria-hidden="true" className="text-rose-600">*</span></label>
              <select id="toolkit-level" required className="input" value={form.level} onChange={event => updateForm('level', event.target.value)}>{['入门', '进阶', '专业'].map(value => <option key={value}>{value}</option>)}</select>
            </div>
            <div>
              <label htmlFor="toolkit-price" className="label">定价（元） <span aria-hidden="true" className="text-rose-600">*</span></label>
              <input id="toolkit-price" type="number" required min="0" max="9999" step="0.01" className="input" value={form.price} onChange={event => updateForm('price', event.target.value)} />
              <p className="mt-2 text-xs text-slate-400">设为 0 即免费；付费订单金额的 20% 计入作者余额。</p>
            </div>
            <div>
              <label htmlFor="toolkit-tags" className="label">标签（选填）</label>
              <input id="toolkit-tags" maxLength={MAX_TAGS_LENGTH} className="input" placeholder="例如：RNA-seq,workflow" value={form.tags} onChange={event => updateForm('tags', event.target.value)} />
              <p className="mt-1 text-xs text-slate-400">最多 300 字；多个标签用英文逗号分隔</p>
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="toolkit-cover" className="label"><Image size={15} className="mr-1.5 inline" />封面图片 URL（选填）</label>
              <input id="toolkit-cover" maxLength={500} className="input" placeholder="https://..." value={form.cover} disabled={!!coverFile} onChange={event => updateForm('cover', event.target.value)} />
              <label htmlFor="toolkit-cover-file" className="label mt-3">或上传封面图片（选填）</label>
              <input id="toolkit-cover-file" type="file" accept=".jpg,.jpeg,.png,.webp,.gif" className="input mt-1" onChange={event => {
                const file = event.currentTarget.files?.[0]
                event.currentTarget.value = ''
                if (file) updateCoverFile(file)
              }} />
              {coverFile && <div className="mt-2"><span className="text-xs text-emerald-700">已选择：{coverFile.name}</span>
                <button type="button" className="ml-3 text-xs text-slate-500 underline" onClick={() => updateCoverFile(null)}>移除封面文件</button>
                <SelectedImage file={coverFile} name="封面缩略图" />
              </div>}
              {coverFileError && <p role="alert" className="mt-2 text-xs text-rose-600">{coverFileError}</p>}
              <p className="mt-1 text-xs text-slate-400">用于工具包列表和详情页；上传文件时暂停使用 URL，移除文件后可恢复。支持 JPG/PNG/WebP/GIF，不支持 SVG。</p>
            </div>
          </div>
        </section>

        <section className="panel p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-black text-ink">图文教程步骤</h2>
              <p className="mt-1 text-xs text-slate-500">每一步对应学习目录中的一节；第 1 步默认开放试看。最多 50 步（{lessons.length}/50）。</p>
            </div>
            <button type="button" disabled={lessons.length >= MAX_LESSONS} className="btn-ghost disabled:cursor-not-allowed disabled:opacity-50" onClick={() => {
              if (lessons.length >= MAX_LESSONS) return
              setLessons(current => [...current, { title: '', content: '', duration: 10, image: '' }])
              setLessonFiles(current => [...current, null])
              setLessonFileErrors(current => [...current, null])
              setLessonImages(current => [...current, null])
              setLessonImageErrors(current => [...current, null])
            }}><Plus size={16} />添加步骤</button>
          </div>
          <div className="mt-6 space-y-5">
            {lessons.map((lesson, index) => (
              <div key={index} className="rounded-2xl border bg-slate-50/50 p-4 sm:p-5">
                <div className="flex items-center justify-between">
                  <span className="chip bg-ink text-white">步骤 {index + 1}{index === 0 && ' · 试看'}</span>
                  {lessons.length > 1 && <button type="button" aria-label={`删除步骤 ${index + 1}`} onClick={() => {
                    setLessons(current => current.filter((_, i) => i !== index))
                    setLessonFiles(current => current.filter((_, i) => i !== index))
                    setLessonFileErrors(current => current.filter((_, i) => i !== index))
                    setLessonImages(current => current.filter((_, i) => i !== index))
                    setLessonImageErrors(current => current.filter((_, i) => i !== index))
                  }} className="text-red-400"><Trash2 size={17} /></button>}
                </div>

                <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_200px]">
                  <div>
                    <label htmlFor={`lesson-title-${index}`} className="label">步骤标题 <span aria-hidden="true" className="text-rose-600">*</span></label>
                    <input id={`lesson-title-${index}`} required maxLength={MAX_LESSON_TITLE_LENGTH} className="input" placeholder="例如：准备输入数据" value={lesson.title} onChange={event => updateLesson(index, 'title', event.target.value)} />
                    <p className="mt-1 text-xs text-slate-400">最多 160 字</p>
                  </div>
                  <div>
                    <label htmlFor={`lesson-duration-${index}`} className="label">预计学习时长 <span aria-hidden="true" className="text-rose-600">*</span></label>
                    <input id={`lesson-duration-${index}`} aria-describedby={`lesson-duration-help-${index}`} required type="number" min="1" max="600" step="1" className="input" value={lesson.duration} onChange={event => updateLesson(index, 'duration', Number(event.target.value))} />
                    <p id={`lesson-duration-help-${index}`} className="mt-1 text-xs text-slate-500">给学习者参考的用时，1–600 分钟整数。</p>
                  </div>
                </div>

                <label htmlFor={`lesson-content-${index}`} className="label mt-4">步骤正文{lessonFiles[index] ? '（原件可代替）' : <span aria-hidden="true" className="text-rose-600"> *</span>}</label>
                <textarea id={`lesson-content-${index}`} required={!lessonFiles[index]} aria-describedby={`lesson-content-help-${index}`} className="input mt-1 min-h-36 resize-y" placeholder="写下操作步骤、命令、参数与注意事项…" value={lesson.content} onChange={event => updateLesson(index, 'content', event.target.value)} />
                <p id={`lesson-content-help-${index}`} className="mt-1 text-xs text-slate-500">上传教程文件后正文可留空，平台会解析文件用于展示；未上传时请填写正文。</p>

                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <label htmlFor={`lesson-file-${index}`} className="btn-ghost cursor-pointer text-xs focus-within:ring-2 focus-within:ring-pine focus-within:ring-offset-2">
                    <UploadCloud size={14} />上传教程文件（选填）
                    <input id={`lesson-file-${index}`} type="file" accept=".md,.markdown,.txt,.docx,.pdf,.html,.htm,.rtf,.pptx" className="sr-only" onChange={event => {
                      const file = event.currentTarget.files?.[0]
                      event.currentTarget.value = ''
                      if (file) updateLessonFile(index, file)
                    }} />
                  </label>
                  {lessonFiles[index] && <span className="text-xs text-emerald-700">已选择：{lessonFiles[index]?.name}</span>}
                  {lessonFiles[index] && <button type="button" className="text-xs text-slate-500 underline" onClick={() => updateLessonFile(index, null)}>移除原件</button>}
                </div>
                <p className="mt-2 text-xs text-slate-400">支持 Markdown/TXT、Word、PDF、PPT、HTML、RTF 文件；单个文件不超过 40 MB。</p>
                {lessonFileErrors[index] && <p role="alert" className="mt-2 text-xs text-rose-600">{lessonFileErrors[index]}</p>}
                {lessonFiles[index] && <details className="mt-2 text-sm"><summary className="cursor-pointer text-emerald-700">预览教程原件</summary><div className="mt-2"><SelectedFilePreview file={lessonFiles[index]} name={lessonFiles[index].name} /></div></details>}

                <label htmlFor={`lesson-image-${index}`} className="label mt-4">本步骤配图 URL（选填）</label>
                <input id={`lesson-image-${index}`} maxLength={500} disabled={!!lessonImages[index]} className="input mt-1" placeholder="https://..." value={lesson.image} onChange={event => updateLesson(index, 'image', event.target.value)} />
                <label htmlFor={`lesson-image-file-${index}`} className="label mt-3">或上传本步骤配图（选填）</label>
                <input id={`lesson-image-file-${index}`} type="file" accept=".jpg,.jpeg,.png,.webp,.gif" className="input mt-1" onChange={event => {
                  const file = event.currentTarget.files?.[0]
                  event.currentTarget.value = ''
                  if (file) updateLessonImage(index, file)
                }} />
                {lessonImages[index] && <div className="mt-2"><span className="text-xs text-emerald-700">已选择：{lessonImages[index].name}</span>
                  <button type="button" className="ml-3 text-xs text-slate-500 underline" onClick={() => updateLessonImage(index, null)}>移除第 {index + 1} 步配图</button>
                  <SelectedImage file={lessonImages[index]} name={`第 ${index + 1} 步配图缩略图`} />
                </div>}
                {lessonImageErrors[index] && <p role="alert" className="mt-2 text-xs text-rose-600">{lessonImageErrors[index]}</p>}
                <p className="mt-1 text-xs text-slate-400">上传文件时暂停使用配图 URL；移除文件后可恢复。仅支持 JPG/PNG/WebP/GIF。</p>
              </div>
            ))}
          </div>
        </section>

        <section className="panel p-6 sm:p-8">
          <h2 className="text-lg font-black text-ink">配套资源（选填）</h2>
          <p id="resource-help" className="mt-1 text-xs text-slate-500">脚本、示例数据等；最多 20 个，每个文件不超过 40 MB，封面、配图、教程原件与资源合计不超过 100 MB。图片/PDF/文字可在上传前预览；Word/PPT 请保存后查看解析结果。</p>
          <label htmlFor="toolkit-resources" className="mt-5 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed p-8 text-center transition hover:border-pine hover:bg-emerald-50/30 focus-within:ring-2 focus-within:ring-pine focus-within:ring-offset-2">
            <UploadCloud className="text-emerald-700" />
            <b className="mt-3 text-sm text-ink">{resources.length ? `已选择 ${resources.length} 个文件` : '点击选择资源文件（可多选）'}</b>
            <span className="mt-1 text-xs text-slate-400">{resources.map((file, index) => <span key={`${file.name}-${file.lastModified}-${index}`}>{file.name}{index < resources.length - 1 ? ' · ' : ''}</span>)}</span>
            <input id="toolkit-resources" aria-describedby="resource-help" type="file" multiple className="sr-only" onChange={event => {
              const selected = Array.from(event.currentTarget.files ?? [])
              event.currentTarget.value = ''
              addResources(selected)
            }} />
          </label>
          {resourceError && <p role="alert" className="mt-3 text-sm text-rose-600">{resourceError}</p>}
          {resources.map((file, index) => <details key={`${file.name}-${file.lastModified}-${index}`} className="mt-2 rounded-xl border p-3 text-sm">
            <summary className="cursor-pointer text-emerald-700">预览资源：{file.name}</summary>
            <div className="mt-2"><SelectedFilePreview file={file} name={file.name} /></div>
          </details>)}
          {resources.length > 0 && <button type="button" className="btn-ghost mt-3" onClick={() => { setResources([]); setResourceError('') }}><Trash2 size={14} />清空已选文件</button>}
        </section>

        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end">
          <Link to="/me/toolkits" className="btn-ghost">取消</Link>
          <p className="text-xs text-slate-400 sm:mr-auto">保存的草稿可之后再发布。</p>
          <button type="button" disabled={!!busy} onClick={() => void submit('draft')} className="btn-primary">{busy === 'draft' ? '正在保存…' : '保存草稿'}</button>
          <button disabled={!!busy} className="btn-mint px-7">{busy === 'published' ? '正在发布…' : '发布工具包'}</button>
        </div>
      </form>
    </div>
  )
}
