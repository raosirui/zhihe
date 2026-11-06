import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, ClipboardCopy, FileArchive, Layers, Loader2, PackageOpen, ScrollText, SquarePen, UploadCloud, Wand2, XCircle } from 'lucide-react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { api, downloadAuthed } from '../../shared/api'
import { cn } from '../../shared/lib'
import { useAuth } from '../../AuthContext'
import { useRequestScope } from '../../useRequestScope'

type ItemResult = { index: number; title: string; status: 'created' | 'updated' | 'failed' | 'skipped'; toolkitId?: number; lessonCount?: number; error?: string }
type JobReport = { jobId: string; status: 'queued' | 'running' | 'done' | 'failed'; mode: string; publish: boolean; total: number; processed: number; created: number; updated: number; skipped: number; failed: number; error?: string | null; results: ItemResult[]; createdAt?: string }

const STRUCTURE = `zhihe-batch.zip
├── toolkits.json      必需 · 工具包清单（UTF-8 JSON 数组）
├── covers/            封面图片（jpg / png / webp / gif / svg，可选）
├── resources/         配套资源（压缩包 / 脚本 / 示例数据，单个 ≤40MB，可选）
└── lessons/           长正文 Markdown，用 contentFile 引用（可选）`

const FIELDS = [
  ['title', '必填 · 名称 ≤120 字'],
  ['description', '必填 · 详细介绍（Markdown）'],
  ['summary / tags', '简介 ≤500 字、标签；featured 无效（推荐位仅管理员设置）'],
  ['category / level', '生信分析·单细胞·空间组学·结构生物学·基因编辑 / 入门·进阶·专业'],
  ['price', '定价 0～9999 元，0 = 免费'],
  ['cover / resource', 'ZIP 内相对路径，或图片 URL；脚本仅作附件下载，不会在平台执行'],
  ['resources[]', '可选数组，最多 20 个配套附件；与 resource 合并去重'],
  ['lessons[]', 'title + content（或 contentFile，单节 ≤1MB）+ duration + preview 试看'],
]

export function BatchPublishPage() {
  const { user, loading, restoreError, refresh } = useAuth(); const navigate = useNavigate(), scope = useRequestScope('batch'), { openAuth } = useOutletContext<{ openAuth: () => void }>()
  const inFlight = useRef(false)
  const [prompt, setPrompt] = useState('')
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [report, setReport] = useState<JobReport>()
  const [mode, setMode] = useState<'skip' | 'update' | 'create'>('skip')
  const [publish, setPublish] = useState(true)
  const pollTimer = useRef<number | undefined>(undefined)
  useEffect(() => {
    setPrompt(''); setFile(undefined); setReport(undefined); setBusy(false); setDrag(false); setMode('skip'); setPublish(true); inFlight.current = false
    return () => { if (pollTimer.current) window.clearTimeout(pollTimer.current) }
  }, [scope.key])
  useEffect(() => {
    if (loading || !user || restoreError) return
    const request = scope.begin('prompt')
    void api<{ text: string }>('/batch/prompt', { signal: request.signal }).then(data => { if (request.current()) setPrompt(data.text) }).catch(() => { if (request.current()) toast.error('提示词加载失败') })
  }, [scope, loading, user?.id, restoreError])
  if (loading) return <div className="p-12 text-center">正在恢复会话…</div>
  if (restoreError) return <div className="p-12 text-center"><p role="alert">{restoreError}</p><button className="btn-primary" onClick={() => void refresh().catch(() => {})}>重试恢复</button></div>
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><PackageOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 text-xl font-black">登录后即可批量上传工具包</h1><button onClick={openAuth} className="btn-primary mt-6">登录 / 注册</button></div>

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
  const poll = async (jobId: string) => {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    try {
      const next = await api<JobReport>(`/batch/jobs/${jobId}`)
      setReport(next)
      if (next.status === 'queued' || next.status === 'running') {
        pollTimer.current = window.setTimeout(() => void poll(jobId), 2000)
      } else {
        inFlight.current = false; setBusy(false)
        if (next.status === 'failed') toast.error(next.error || '批量任务失败')
        else {
          const createdCount = next.created + next.updated
          createdCount ? toast.success(`处理完成：新建 ${next.created} · 更新 ${next.updated} · 跳过 ${next.skipped} · 失败 ${next.failed}`) : toast.warning('没有成功条目，请查看下方原因')
        }
      }
    } catch (e) {
      inFlight.current = false; setBusy(false)
      if (pollTimer.current) pollTimer.current = window.setTimeout(() => void poll(jobId), 5000)
      toast.error((e as Error).message || '进度读取失败，将自动重试')
    }
  }
  const upload = async () => {
    if (inFlight.current) return
    if (!file) return toast.error('请先选择要上传的 ZIP 文件')
    inFlight.current = true; setBusy(true); setReport(undefined); const request = scope.begin('upload')
    try {
      const body = new FormData(); body.append('package', file); body.append('mode', mode); body.append('publish', publish ? '1' : '0')
      const result = await api<{ jobId: string }>('/batch/upload', { method: 'POST', body, signal: request.signal })
      if (!request.current()) { inFlight.current = false; setBusy(false); return }
      setReport({ jobId: result.jobId, status: 'queued', mode, publish, total: 0, processed: 0, created: 0, updated: 0, skipped: 0, failed: 0, results: [] })
      toast.success('已开始后台处理，可离开页面稍后回来查看')
      void poll(result.jobId)
    } catch (e) { if (request.current()) { inFlight.current = false; setBusy(false); toast.error((e as Error).message) } }
  }
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
        <div className="rounded-2xl border bg-slate-50/60 p-4">
          <label className="text-xs font-bold text-slate-500">同名工具包处理方式</label>
          <div className="mt-2 flex gap-2">
            {([['skip', '跳过（只建新）'], ['update', '覆盖更新'], ['create', '允许重名新建']] as const).map(([value, label]) =>
              <button key={value} type="button" disabled={busy} onClick={() => setMode(value)} className={cn('rounded-xl border px-3 py-1.5 text-xs font-bold transition', mode === value ? 'border-pine bg-ink text-white' : 'text-slate-500 hover:bg-white')}>{label}</button>)}
          </div>
        </div>
        <div className="rounded-2xl border bg-slate-50/60 p-4">
          <label className="text-xs font-bold text-slate-500">创建后的状态</label>
          <div className="mt-2 flex gap-2">
            {([[true, '直接上架'], [false, '存为草稿']] as const).map(([value, label]) =>
              <button key={String(value)} type="button" disabled={busy} onClick={() => setPublish(value)} className={cn('rounded-xl border px-3 py-1.5 text-xs font-bold transition', publish === value ? 'border-pine bg-ink text-white' : 'text-slate-500 hover:bg-white')}>{label}</button>)}
          </div>
          <p className="mt-2 text-[11px] leading-4 text-slate-400">草稿仅自己可见，可在「我的工具包」一键发布</p>
        </div>
      </div>
      <label onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); if (busy) return; setDrag(false); const f = e.dataTransfer.files?.[0]; if (f?.name.toLowerCase().endsWith('.zip')) setFile(f); else if (f) toast.error('仅支持 .zip 文件') }}
        className={cn('mt-4 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed p-8 text-center transition', drag ? 'border-pine bg-emerald-50/60' : 'hover:border-pine hover:bg-emerald-50/30')}>
        <UploadCloud className="text-emerald-700" />
        <b className="mt-3 text-sm text-ink">{file ? file.name : '点击选择或拖拽 ZIP 文件到此处'}</b>
        <span className="mt-1 text-xs text-slate-400">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : '须包含 toolkits.json，最大 100MB'}</span>
        <input disabled={busy} type="file" accept=".zip,application/zip" className="hidden" onChange={e => { setFile(e.target.files?.[0]); setReport(undefined) }} />
      </label>
      <div className="mt-5 flex justify-end">
        <button disabled={busy || !file} onClick={upload} className="btn-mint px-7">{busy ? <><Loader2 size={16} className="animate-spin" />正在处理…</> : <>开始批量上传<ArrowRight size={16} /></>}</button>
      </div>
      {report && <div className="mt-6">
        {(report.status === 'queued' || report.status === 'running') &&
          <p role="status" className="flex items-center gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-sm font-bold text-emerald-800"><Loader2 size={15} className="animate-spin" />后台处理中 {report.processed}/{report.total || '…'}，可离开此页面，稍后回来查看</p>}
        {report.status === 'failed' && <p role="alert" className="rounded-2xl bg-red-50 px-4 py-3 text-sm font-bold text-red-600">{report.error || '批量任务失败，请重新上传'}</p>}
        {report.status === 'done' &&
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
            <p className="text-sm font-bold text-ink">共 {report.total} 条 · 创建 {report.created} · 更新 {report.updated} · 跳过 {report.skipped} · 失败 {report.failed}</p>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => { setReport(undefined); setFile(undefined) }}>重新上传</button>
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
  </div>
}

const StepHead = ({ n, title, desc }: { n: number; title: string; desc: string }) =>
  <div className="flex items-start gap-4"><span className="grid size-9 shrink-0 place-items-center rounded-2xl bg-ink text-sm font-black text-mint">{n}</span><div><h2 className="text-lg font-black text-ink">{title}</h2><p className="mt-1 text-xs text-slate-400">{desc}</p></div></div>
