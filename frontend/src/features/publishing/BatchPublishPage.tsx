import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, ClipboardCopy, FileArchive, Layers, Loader2, PackageOpen, ScrollText, SquarePen, UploadCloud, Wand2, XCircle } from 'lucide-react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { api, downloadAuthed } from '../../shared/api'
import { cn } from '../../shared/lib'
import { useAuth } from '../../AuthContext'
import { useRequestScope } from '../../useRequestScope'

type ItemResult = { index: number; title: string; status: 'created' | 'updated' | 'failed' | 'skipped'; toolkitId?: number; lessonCount?: number; error?: string }
type JobSummary = { jobId: string; status: 'queued' | 'running' | 'done' | 'failed'; mode: 'skip' | 'update' | 'create'; publish: boolean; total: number; processed: number; created: number; updated: number; skipped: number; failed: number; error?: string | null; createdAt?: string | null }
type JobReport = JobSummary & { results: ItemResult[] }
const MAX_ZIP_BYTES = 100 * 1024 * 1024 - 64 * 1024
const jobStatus = { queued: '排队中', running: '处理中', done: '已完成', failed: '任务失败' }
const jobMode = { skip: '跳过同名', update: '覆盖同名', create: '允许同名' }

const STRUCTURE = `zhihe-batch.zip
├── toolkits.json      必需 · 工具包清单（UTF-8 JSON 数组）
├── covers/            封面图片（jpg / png / webp / gif / svg，可选）
├── resources/         配套资源（压缩包 / 脚本 / 示例数据，单个 ≤40MB，可选）
└── lessons/           步骤正文、配图及原件，可用 contentFile / documentFile 引用（可选）`

const FIELDS = [
  ['title', '必填 · 名称 ≤120 字'],
  ['description', '必填 · 详细介绍（Markdown）'],
  ['summary / tags', '简介 ≤500 字、标签；featured 无效（推荐位仅管理员设置）'],
  ['category / level', '生信分析·单细胞·空间组学·结构生物学·基因编辑 / 入门·进阶·专业'],
  ['price', '定价 0～9999 元，0 = 免费'],
  ['cover / image', 'ZIP 内相对图片路径，或 http(s) 图片 URL'],
  ['resource / resources[]', '仅 ZIP 内相对文件路径，不支持 URL；每个工具包最多 20 个附件，脚本不会执行'],
  ['lessons[]', '每包最多 50 节；新建至少一节才能上架，更新省略 lessons 保留旧步骤，显式 [] 只能清空草稿'],
  ['contentFile / documentFile', 'contentFile 为 UTF-8 正文路径（≤1MB）；documentFile 为原件路径，支持 md、markdown、txt、docx、pdf、html、htm、rtf、pptx，Markdown 类原件≤1MB，不支持 URL'],
  ['clearDocument', '更新须提供非空正文并设为 true 才清除旧原件；省略保留原件与附件；保留非 Markdown 原件时不能只改正文，需替换或清除原件'],
]

export function BatchPublishPage() {
  const { user, loading, restoreError, refresh } = useAuth(); const navigate = useNavigate(), scope = useRequestScope('batch'), { openAuth } = useOutletContext<{ openAuth: () => void }>()
  const inFlight = useRef(false)
  const pollTimer = useRef<number | undefined>(undefined)
  const activeJob = useRef<string | undefined>(undefined)
  const fileInput = useRef<HTMLInputElement>(null)
  const [prompt, setPrompt] = useState('')
  const [fileState, setFileState] = useState<{ key: string; file: File }>()
  const [selectionError, setSelectionError] = useState<{ key: string; message: string }>()
  const [busyKey, setBusyKey] = useState<string>()
  const [uploadingKey, setUploadingKey] = useState<string>()
  const [watching, setWatching] = useState<{ key: string; jobId: string }>()
  const [pollError, setPollError] = useState('')
  const [drag, setDrag] = useState(false)
  const [reportState, setReportState] = useState<{ key: string; data: JobReport }>()
  const [jobsState, setJobsState] = useState<{ key: string; items: JobSummary[]; loading: boolean; error: string }>()
  const [mode, setMode] = useState<'skip' | 'update' | 'create'>('skip')
  const [publish, setPublish] = useState(true)
  const file = fileState?.key === scope.key ? fileState.file : undefined
  const report = reportState?.key === scope.key ? reportState.data : undefined
  const jobs = jobsState?.key === scope.key ? jobsState : undefined
  const busy = busyKey === scope.key
  const uploading = uploadingKey === scope.key
  const watchingJob = watching?.key === scope.key ? watching.jobId : undefined

  useEffect(() => {
    setPrompt(''); setFileState(undefined); setSelectionError(undefined); setReportState(undefined); setJobsState(undefined)
    setBusyKey(undefined); setUploadingKey(undefined); setWatching(undefined); setPollError(''); setDrag(false)
    setMode('skip'); setPublish(true); inFlight.current = false; activeJob.current = undefined
    if (fileInput.current) fileInput.current.value = ''
    return () => { if (pollTimer.current !== undefined) window.clearTimeout(pollTimer.current); pollTimer.current = undefined }
  }, [scope.key])
  useEffect(() => {
    if (loading || !user || restoreError) return
    const request = scope.begin('prompt')
    void api<{ text: string }>('/batch/prompt', { signal: request.signal }).then(data => { if (request.current()) setPrompt(data.text) }).catch(() => { if (request.current()) toast.error('提示词加载失败') })
  }, [scope, loading, user?.id, restoreError])
  useEffect(() => {
    if (loading || !user || restoreError) return
    void loadJobs()
  }, [scope, loading, user?.id, restoreError])

  const loadJobs = async () => {
    const request = scope.begin('jobs')
    setJobsState(prev => ({ key: scope.key, items: prev?.key === scope.key ? prev.items : [], loading: true, error: '' }))
    try {
      const items = await api<JobSummary[]>('/batch/jobs', { signal: request.signal })
      if (request.current()) setJobsState({ key: scope.key, items, loading: false, error: '' })
    } catch (e) {
      if (request.current()) setJobsState(prev => ({ key: scope.key, items: prev?.key === scope.key ? prev.items : [], loading: false, error: (e as Error).message || '任务列表加载失败' }))
    }
  }
  const stopWatching = () => {
    if (pollTimer.current !== undefined) window.clearTimeout(pollTimer.current)
    pollTimer.current = undefined
    activeJob.current = undefined
    scope.begin('job') // Supersede even a fetch that ignores abort.
    setWatching(undefined); setPollError(''); setBusyKey(undefined)
  }
  const prepareNew = () => {
    stopWatching(); setReportState(undefined); setFileState(undefined); setSelectionError(undefined)
    if (fileInput.current) fileInput.current.value = ''
  }
  const chooseFile = (next?: File) => {
    if (!next || busy) return
    stopWatching(); setReportState(undefined); setFileState(undefined); setSelectionError(undefined)
    const message = !next.name.toLowerCase().endsWith('.zip') ? '仅支持 .zip 文件' : next.size > MAX_ZIP_BYTES ? 'ZIP 文件不能超过约 99.94 MB（需为上传表单预留 64 KB）' : ''
    if (message) setSelectionError({ key: scope.key, message })
    else setFileState({ key: scope.key, file: next })
  }
  const copyPrompt = async () => {
    if (!prompt) return toast.error('提示词尚未加载')
    const request = scope.begin('clipboard')
    try { await navigator.clipboard.writeText(prompt); if (request.current()) toast.success('转换提示词已复制，粘贴给本地 AI 助手即可') }
    catch { if (request.current()) toast.error('浏览器不支持自动复制，请手动选中下方文本') }
  }
  const download = async (path: string, filename: string) => {
    const request = scope.begin('download')
    try { await downloadAuthed(path, filename, request) } catch (e) { if (request.current()) toast.error((e as Error).message) }
  }
  const poll = async (jobId: string, notifyCompletion: boolean) => {
    if (pollTimer.current !== undefined) window.clearTimeout(pollTimer.current)
    pollTimer.current = undefined
    const request = scope.begin('job')
    try {
      const next = await api<JobReport>(`/batch/jobs/${jobId}`, { signal: request.signal })
      if (!request.current() || activeJob.current !== jobId) return
      setReportState({ key: scope.key, data: next }); setPollError('')
      setJobsState(prev => prev?.key === scope.key ? {
        ...prev, items: prev.items.map(job => job.jobId === jobId ? {
          ...job, status: next.status, total: next.total, processed: next.processed,
          created: next.created, updated: next.updated, skipped: next.skipped, failed: next.failed, error: next.error,
        } : job),
      } : prev)
      if (next.status === 'queued' || next.status === 'running') {
        pollTimer.current = window.setTimeout(() => void poll(jobId, notifyCompletion), 2000)
      } else {
        activeJob.current = undefined; setWatching(undefined); setBusyKey(undefined)
        void loadJobs()
        if (notifyCompletion) {
          if (next.status === 'failed') toast.error(next.error || '批量任务失败')
          else if (next.created + next.updated) toast.success(`处理完成：新建 ${next.created} · 更新 ${next.updated} · 跳过 ${next.skipped} · 失败 ${next.failed}`)
          else toast.warning('没有成功条目，请查看下方原因')
        }
      }
    } catch (e) {
      if (!request.current() || activeJob.current !== jobId) return
      setPollError(`进度读取失败，将在 5 秒后自动重试：${(e as Error).message || '网络错误'}`)
      pollTimer.current = window.setTimeout(() => void poll(jobId, notifyCompletion), 5000)
    }
  }
  const openJob = (jobId: string, notifyCompletion = false, initial?: JobReport) => {
    if (uploading) return
    stopWatching(); activeJob.current = jobId
    setReportState(initial ? { key: scope.key, data: initial } : undefined)
    setWatching({ key: scope.key, jobId }); setBusyKey(scope.key)
    void poll(jobId, notifyCompletion)
  }
  const upload = async () => {
    if (inFlight.current || busy) return
    if (!file) return toast.error('请先选择要上传的 ZIP 文件')
    if ((mode === 'update' || publish) && !window.confirm(`${mode === 'update' ? '将覆盖本人同名工具包的内容，保留原有上架状态。' : ''}${publish ? '新建的工具包将直接公开上架。' : '新建的工具包将存为草稿。'}确认上传？`)) return
    inFlight.current = true; setUploadingKey(scope.key); setBusyKey(scope.key); setReportState(undefined)
    const request = scope.begin('upload')
    try {
      const body = new FormData(); body.append('package', file); body.append('mode', mode); body.append('publish', publish ? '1' : '0')
      const result = await api<{ jobId: string }>('/batch/upload', { method: 'POST', body, signal: request.signal })
      if (!request.current()) return
      setJobsState(prev => prev?.key === scope.key ? { ...prev, items: [{ jobId: result.jobId, status: 'queued', mode, publish, total: 0, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0 }, ...prev.items.filter(job => job.jobId !== result.jobId)] } : prev)
      toast.success('已开始后台处理，可离开页面稍后回来查看')
      // The POST is finished; allow switching to another saved job while this one polls.
      inFlight.current = false; setUploadingKey(undefined)
      openJob(result.jobId, true, { jobId: result.jobId, status: 'queued', mode, publish, total: 0, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0, results: [] })
      void loadJobs()
    } catch (e) {
      if (request.current()) { inFlight.current = false; setUploadingKey(undefined); setBusyKey(undefined); toast.error((e as Error).message) }
    }
  }
  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <div className="p-12 text-center"><p role="alert">{restoreError}</p><button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><PackageOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 text-xl font-black">登录后即可批量上传工具包</h1><button onClick={openAuth} className="btn-primary mt-6">登录 / 注册</button></div>
  const statusStyle = { created: ['bg-emerald-100 text-emerald-700', CheckCircle2, '已创建'], updated: ['bg-sky-100 text-sky-700', ArrowRight, '已更新'], skipped: ['bg-amber-100 text-amber-700', Layers, '已跳过'], failed: ['bg-red-100 text-red-600', XCircle, '失败'] } as const

  return <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:py-12">
    <Link to="/me/toolkits" className="inline-flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} />返回我的工具包</Link>
    <div className="mt-5 flex flex-wrap items-end justify-between gap-3">
      <div><p className="text-sm font-bold text-emerald-700">BATCH CREATE</p><h1 className="mt-1 text-3xl font-black text-ink">批量上传工具包</h1><p className="mt-2 text-sm text-slate-500">按标准格式整理多个工具包，一次上传自动创建。</p></div>
      <Link to="/publish" className="btn-ghost">单个发布<SquarePen size={16} /></Link>
    </div>

    <section className="panel mt-8 p-6 sm:p-8">
      <StepHead n={1} title="获取标准格式" desc="任选一种方式，在本地把资料转换成标准 ZIP" />
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border bg-slate-50/60 p-5">
          <FileArchive className="text-emerald-700" size={22} />
          <p className="mt-3 text-sm font-black text-ink">标准模板 ZIP</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">含示例 toolkits.json、封面与资源目录，照抄即可。</p>
          <button className="btn-primary mt-4 w-full" onClick={() => void download('/batch/template', 'zhihe-standard-template.zip')}>下载模板</button>
        </div>
        <div className="rounded-2xl border bg-slate-50/60 p-5">
          <Wand2 className="text-violet-600" />
          <p className="mt-3 text-sm font-black text-ink">本地转换 Skill</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">Claude Code 技能，解压到 <code className="rounded bg-white px-1">~/.claude/skills/</code>，让本地 AI 帮你转换打包。</p>
          <button className="btn-mint mt-4 w-full" onClick={() => void download('/batch/skill', 'zhihe-toolkit-converter-skill.zip')}>下载 Skill 包</button>
        </div>
        <div className="rounded-2xl border bg-slate-50/60 p-5">
          <ScrollText className="text-amber-600" />
          <p className="mt-3 text-sm font-black text-ink">转换提示词</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">粘贴给任意 AI 助手，把教程资料整理成标准格式。</p>
          <button className="btn-ghost mt-4 w-full" onClick={copyPrompt}><ClipboardCopy size={16} />复制提示词</button>
        </div>
      </div>
      {prompt && <details className="mt-4 rounded-2xl border bg-white p-4"><summary className="cursor-pointer text-xs font-bold text-slate-500">查看提示词全文</summary><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-6 text-slate-600">{prompt}</pre></details>}
    </section>

    <section className="panel mt-6 p-6 sm:p-8">
      <StepHead n={2} title="本地转换成标准格式" desc="在 ZIP 根目录放 toolkits.json，封面、资源与长正文按目录组织" />
      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <pre className="overflow-x-auto rounded-2xl bg-ink p-5 text-xs leading-6 text-mint">{STRUCTURE}</pre>
        <ul className="space-y-2 text-xs leading-5 text-slate-500">{FIELDS.map(([k, v]) => <li key={k} className="flex gap-2"><b className="shrink-0 font-mono text-emerald-700">{k}</b><span>{v}</span></li>)}</ul>
      </div>
      <p className="mt-4 rounded-2xl bg-emerald-50 px-4 py-3 text-xs leading-5 text-emerald-800">使用 Skill 包时可运行：<code className="font-mono">python3 scripts/build_batch.py validate &lt;目录&gt;</code> 自检，再用 <code className="font-mono">pack</code> 命令打包。一次最多 50 个工具包，资源单个 ≤40MB，整包解压后 ≤300MB。</p>
    </section>

    <section className="panel mt-6 p-6 sm:p-8">
      <StepHead n={3} title="上传标准 ZIP" desc="上传后由后台逐条处理，实时显示每一条的进度与结果" />
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <fieldset className="rounded-2xl border bg-slate-50/60 p-4">
          <legend className="text-xs font-bold text-slate-500">同名工具包处理方式</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {([['skip', '跳过（只建新）'], ['update', '覆盖更新'], ['create', '允许重名新建']] as const).map(([value, label]) =>
              <label key={value} className={cn('cursor-pointer rounded-xl border px-3 py-1.5 text-xs font-bold transition focus-within:ring-2 focus-within:ring-pine', mode === value ? 'border-pine bg-ink text-white' : 'text-slate-500 hover:bg-white', busy && 'cursor-not-allowed opacity-60')}>
                <input className="sr-only" type="radio" name="batch-mode" value={value} checked={mode === value} disabled={busy} onChange={() => setMode(value)} />{label}
              </label>)}
          </div>
          <p className="mt-2 text-[11px] leading-4 text-slate-500">按本人同名标题判断：跳过不改旧包；覆盖更新会修改旧包内容（原上架状态不变）；允许重名新建会创建另一份。</p>
        </fieldset>
        <fieldset className="rounded-2xl border bg-slate-50/60 p-4">
          <legend className="text-xs font-bold text-slate-500">新建工具包的状态</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {([[true, '直接上架'], [false, '存为草稿']] as const).map(([value, label]) =>
              <label key={String(value)} className={cn('cursor-pointer rounded-xl border px-3 py-1.5 text-xs font-bold transition focus-within:ring-2 focus-within:ring-pine', publish === value ? 'border-pine bg-ink text-white' : 'text-slate-500 hover:bg-white', busy && 'cursor-not-allowed opacity-60')}>
                <input className="sr-only" type="radio" name="batch-publish" value={String(value)} checked={publish === value} disabled={busy} onChange={() => setPublish(value)} />{label}
              </label>)}
          </div>
          <p className="mt-2 text-[11px] leading-4 text-slate-500">仅对新建条目生效：直接上架公开可见；草稿仅自己可见，可在「我的工具包」发布。覆盖更新不改变旧包状态。</p>
        </fieldset>
      </div>
      <label onDragOver={e => { e.preventDefault(); if (!busy) setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); chooseFile(e.dataTransfer.files?.[0]) }}
        className={cn('mt-4 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed p-8 text-center transition focus-within:ring-2 focus-within:ring-pine', busy ? 'cursor-not-allowed opacity-60' : drag ? 'border-pine bg-emerald-50/60' : 'hover:border-pine hover:bg-emerald-50/30')}>
        <UploadCloud className="text-emerald-700" />
        <b className="mt-3 text-sm text-ink">{file ? file.name : '点击选择或拖拽 ZIP 文件到此处'}</b>
        <span className="mt-1 text-xs text-slate-400">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : '须包含 toolkits.json，ZIP 最多约 99.94 MB（预留 64 KB 表单空间）'}</span>
        <input ref={fileInput} disabled={busy} aria-label="选择批量上传 ZIP 文件" type="file" accept=".zip,application/zip" className="sr-only" onChange={e => { chooseFile(e.currentTarget.files?.[0]); e.currentTarget.value = '' }} />
      </label>
      {selectionError?.key === scope.key && <p role="alert" className="mt-2 text-sm text-red-600">{selectionError.message}</p>}
      <div className="mt-5 flex justify-end">
        <button disabled={busy || !file} onClick={upload} className="btn-mint px-7">{busy ? <><Loader2 size={16} className="animate-spin" />{uploading ? '正在上传…' : '正在查看进度…'}</> : <>开始批量上传<ArrowRight size={16} /></>}</button>
      </div>
      {watchingJob && <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {!report && <p role="status" className="text-sm text-slate-600">正在读取任务 {watchingJob.slice(0, 8)} 的进度…</p>}
        <button type="button" className="btn-ghost" onClick={prepareNew}>停止查看，准备新包</button>
      </div>}
      {watchingJob && pollError && <p role="alert" className="mt-2 text-sm text-red-600">{pollError}</p>}
      {report && <div className="mt-6">
        {(report.status === 'queued' || report.status === 'running') &&
          <p role="status" className="flex items-center gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-sm font-bold text-emerald-800"><Loader2 size={15} className="animate-spin" />后台处理中 {report.processed}/{report.total || '…'}，可离开此页面，稍后回来查看</p>}
        {report.status === 'failed' && <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-red-50 px-4 py-3"><p role="alert" className="text-sm font-bold text-red-600">{report.error || '批量任务失败，请重新上传'}</p><button type="button" className="btn-ghost" onClick={prepareNew}>重新上传</button></div>}
        {report.status === 'done' &&
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
            <p className="text-sm font-bold text-ink">共 {report.total} 条 · 创建 {report.created} · 更新 {report.updated} · 跳过 {report.skipped} · 失败 {report.failed}</p>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={prepareNew}>重新上传</button>
              {(report.created + report.updated) > 0 && <button className="btn-primary" onClick={() => navigate('/me/toolkits')}>查看我的工具包</button>}
            </div>
          </div>}
        <div className="mt-3 space-y-2">{report.results.map(x => {
          const [chip, Icon, label] = statusStyle[x.status]
          return <div key={x.index} className="flex items-center gap-3 rounded-2xl border bg-white p-3.5">
            <span className={cn('chip shrink-0', chip)}><Icon size={13} />{label}</span>
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-ink">{x.title}</p>{x.error && <p className="mt-0.5 text-xs text-red-500">{x.error}</p>}</div>
            {x.toolkitId && <Link to={`/toolkits/${x.toolkitId}`} className="shrink-0 text-xs font-bold text-emerald-700">{x.status === 'created' || x.status === 'updated' ? `${x.lessonCount ?? 0} 个步骤 · 查看` : '查看'}</Link>}
          </div>
        })}</div>
      </div>}
    </section>
    <section className="panel mt-6 p-6 sm:p-8" aria-labelledby="recent-batch-jobs">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="recent-batch-jobs" className="text-lg font-black text-ink">最近的批量任务</h2><p className="mt-1 text-xs text-slate-500">仅显示当前账号最近 20 条；离开页面后可重新打开并继续查看进度和逐条结果。</p></div><button type="button" className="btn-ghost" disabled={jobs?.loading} onClick={() => void loadJobs()}>刷新任务</button></div>
      {jobs?.error && <p role="alert" className="mt-3 text-sm text-red-600">{jobs.error}</p>}
      {(!jobs || jobs.loading) && !jobs?.items.length && <p role="status" className="mt-4 text-sm text-slate-500">正在加载任务…</p>}
      {jobs && !jobs.loading && !jobs.items.length && <p className="mt-4 text-sm text-slate-500">暂无批量任务</p>}
      {!!jobs?.items.length && <ul className="mt-4 space-y-2">{jobs.items.map(job => <li key={job.jobId} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-white p-4">
        <div className="min-w-0"><p className="text-sm font-bold text-ink">{jobStatus[job.status]} · {jobMode[job.mode]} · {job.publish ? '新建直接上架' : '新建为草稿'}</p><p className="mt-1 text-xs text-slate-500">{job.createdAt ? new Date(job.createdAt).toLocaleString('zh-CN') : '刚刚'} · {job.processed}/{job.total || '…'} 条 · 创建 {job.created} · 更新 {job.updated} · 跳过 {job.skipped} · 失败 {job.failed}</p></div>
        <button type="button" className="btn-ghost" disabled={uploading} aria-current={report?.jobId === job.jobId ? 'true' : undefined} onClick={() => openJob(job.jobId)}>查看任务</button>
      </li>)}</ul>}
    </section>
  </div>
}

const StepHead = ({ n, title, desc }: { n: number; title: string; desc: string }) =>
  <div className="flex items-start gap-4"><span className="grid size-9 shrink-0 place-items-center rounded-2xl bg-ink text-sm font-black text-mint">{n}</span><div><h2 className="text-lg font-black text-ink">{title}</h2><p className="mt-1 text-xs text-slate-400">{desc}</p></div></div>
