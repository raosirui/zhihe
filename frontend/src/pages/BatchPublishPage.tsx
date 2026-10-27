import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, CheckCircle2, ClipboardCopy, FileArchive, Layers, Loader2, PackageOpen, ScrollText, SquarePen, UploadCloud, Wand2, XCircle } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { api, cn, downloadAuthed } from '../lib'
import { useAuth } from '../AuthContext'

type ItemResult = { index: number; title: string; status: 'created' | 'failed' | 'skipped'; toolkitId?: number; lessonCount?: number; error?: string }
type Report = { total: number; created: number; failed: number; results: ItemResult[] }

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
  ['cover / resource', 'ZIP 内相对路径，或图片 URL；资源不能是可执行文件类型'],
  ['lessons[]', 'title + content（或 contentFile，单节 ≤1MB）+ duration + preview 试看'],
]

export function BatchPublishPage() {
  const { user } = useAuth(); const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [report, setReport] = useState<Report>()
  useEffect(() => { api<{ text: string }>('/batch/prompt').then(data => setPrompt(data.text)).catch(() => toast.error('提示词加载失败')) }, [])
  if (!user) return <div className="mx-auto max-w-xl px-4 py-20 text-center"><PackageOpen className="mx-auto text-slate-300" size={40} /><h1 className="mt-4 text-xl font-black">登录后即可批量上传工具包</h1><Link to="/" className="btn-primary mt-6">返回首页</Link></div>

  const copyPrompt = async () => {
    if (!prompt) return toast.error('提示词尚未加载')
    try { await navigator.clipboard.writeText(prompt); toast.success('转换提示词已复制，粘贴给本地 AI 助手即可') }
    catch { toast.error('浏览器不支持自动复制，请手动选中下方文本') }
  }
  const upload = async () => {
    if (!file) return toast.error('请先选择要上传的 ZIP 文件')
    setBusy(true); setReport(undefined)
    try {
      const body = new FormData(); body.append('package', file)
      const result = await api<Report>('/batch/upload', { method: 'POST', body })
      setReport(result)
      result.created ? toast.success(`成功创建 ${result.created} 个工具包`) : toast.warning('没有新建工具包，请查看下方原因')
    } catch (e) { toast.error((e as Error).message) } finally { setBusy(false) }
  }
  const statusStyle = { created: ['bg-emerald-100 text-emerald-700', CheckCircle2, '已创建'], skipped: ['bg-amber-100 text-amber-700', Layers, '已跳过'], failed: ['bg-red-100 text-red-600', XCircle, '失败'] } as const

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
          <button className="btn-primary mt-4 w-full" onClick={() => downloadAuthed('/batch/template', 'zhihe-standard-template.zip').catch(e => toast.error((e as Error).message))}>下载模板</button>
        </div>
        <div className="rounded-2xl border bg-slate-50/60 p-5">
          <Wand2 className="text-violet-600" size={22} />
          <p className="mt-3 text-sm font-black text-ink">本地转换 Skill</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">Claude Code 技能，解压到 <code className="rounded bg-white px-1">~/.claude/skills/</code>，让本地 AI 帮你转换打包。</p>
          <button className="btn-mint mt-4 w-full" onClick={() => downloadAuthed('/batch/skill', 'zhihe-toolkit-converter-skill.zip').catch(e => toast.error((e as Error).message))}>下载 Skill 包</button>
        </div>
        <div className="rounded-2xl border bg-slate-50/60 p-5">
          <ScrollText className="text-amber-600" size={22} />
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
      <StepHead n={3} title="上传标准 ZIP" desc="上传后逐条创建工具包，并给出具体的成功 / 失败报告" />
      <label onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f?.name.toLowerCase().endsWith('.zip')) setFile(f); else if (f) toast.error('仅支持 .zip 文件') }}
        className={cn('mt-6 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed p-8 text-center transition', drag ? 'border-pine bg-emerald-50/60' : 'hover:border-pine hover:bg-emerald-50/30')}>
        <UploadCloud className="text-emerald-700" />
        <b className="mt-3 text-sm text-ink">{file ? file.name : '点击选择或拖拽 ZIP 文件到此处'}</b>
        <span className="mt-1 text-xs text-slate-400">{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : '须包含 toolkits.json，最大 100MB'}</span>
        <input type="file" accept=".zip,application/zip" className="hidden" onChange={e => { setFile(e.target.files?.[0]); setReport(undefined) }} />
      </label>
      <div className="mt-5 flex justify-end">
        <button disabled={busy || !file} onClick={upload} className="btn-mint px-7">{busy ? <><Loader2 size={16} className="animate-spin" />正在解析上传…</> : <>开始批量上传<ArrowRight size={16} /></>}</button>
      </div>
      {report && <div className="mt-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
          <p className="text-sm font-bold text-ink">共 {report.total} 条 · 成功 {report.created} · 未创建 {report.failed}</p>
          <div className="flex gap-2">
            <button className="btn-ghost" onClick={() => { setReport(undefined); setFile(undefined) }}>重新上传</button>
            {report.created > 0 && <button className="btn-primary" onClick={() => navigate('/me/toolkits')}>查看我的工具包</button>}
          </div>
        </div>
        <div className="mt-3 space-y-2">{report.results.map(x => {
          const [chip, Icon, label] = statusStyle[x.status]
          return <div key={x.index} className="flex items-center gap-3 rounded-2xl border bg-white p-3.5">
            <span className={cn('chip shrink-0', chip)}><Icon size={13} />{label}</span>
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold text-ink">{x.title}</p>{x.error && <p className="mt-0.5 text-xs text-red-500">{x.error}</p>}</div>
            {x.toolkitId && <Link to={`/toolkits/${x.toolkitId}`} className="shrink-0 text-xs font-bold text-emerald-700">{x.status === 'created' ? `${x.lessonCount ?? 0} 个步骤 · 查看` : '查看'}</Link>}
          </div>
        })}</div>
      </div>}
    </section>
  </div>
}

const StepHead = ({ n, title, desc }: { n: number; title: string; desc: string }) =>
  <div className="flex items-start gap-4"><span className="grid size-9 shrink-0 place-items-center rounded-2xl bg-ink text-sm font-black text-mint">{n}</span><div><h2 className="text-lg font-black text-ink">{title}</h2><p className="mt-1 text-xs text-slate-400">{desc}</p></div></div>
