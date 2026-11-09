import { lazy, Suspense, useEffect, useState } from 'react'

const LocalPdfPreview = lazy(() => import('./LocalPdfPreview'))

const MAX_TEXT_PREVIEW = 128 * 1024
const imageTypes = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif'])
const imageExtensions = /\.(?:jpe?g|png|webp|gif)$/i
const textExtensions = /\.(?:txt|md|markdown|csv|tsv|json|xml|html?|css|js|py|r|sh|yaml|yml|log)$/i

function useObjectUrl(file: Blob | null) {
  const [current, setCurrent] = useState<{ file: Blob; url: string }>()
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setCurrent({ file, url })
    return () => URL.revokeObjectURL(url)
  }, [file])
  return current?.file === file ? current.url : undefined
}

export function SelectedImage({ file, name }: { file: File; name?: string }) {
  const url = useObjectUrl(file)
  if (!url) return <span role="status" className="text-xs text-slate-500">正在生成缩略图…</span>
  return <img className="mt-2 h-28 max-w-full rounded-lg border object-contain" src={url} alt={name || file.name} />
}

/** Local files and resource responses are never interpreted as executable HTML. */
export function SelectedFilePreview({ file, name }: { file: Blob; name: string }) {
  const isImage = imageTypes.has(file.type.toLowerCase()) && imageExtensions.test(name)
  const isPdf = file.type === 'application/pdf' || (/\.pdf$/i.test(name) && (!file.type || file.type === 'application/octet-stream'))
  const isText = !isImage && !isPdf && (file.type.startsWith('text/') || textExtensions.test(name))
  const url = useObjectUrl(isImage || isPdf ? file : null)
  const [preview, setPreview] = useState<{ file: Blob; text: string }>()
  const [readError, setReadError] = useState(false)
  useEffect(() => {
    if (!isText) return
    setReadError(false)
    let active = true
    const reader = new FileReader()
    reader.onload = () => { if (active) setPreview({ file, text: String(reader.result ?? '') }) }
    reader.onerror = () => { if (active) setReadError(true) }
    reader.readAsText(file.slice(0, MAX_TEXT_PREVIEW))
    return () => { active = false; if (reader.readyState === FileReader.LOADING) reader.abort() }
  }, [file, isText])
  if (isImage) return url ? <img className="max-h-[70vh] max-w-full object-contain" alt={name} src={url} /> : <p role="status">正在准备图片预览…</p>
  if (isPdf) return url ? <Suspense fallback={<p role="status">正在准备 PDF 阅读器…</p>}><LocalPdfPreview key={url} url={url} /></Suspense> : <p role="status">正在准备 PDF 预览…</p>
  if (isText) return readError ? <p role="alert">无法读取文字预览，可下载文件查看。</p> :
    preview?.file === file ? <div><pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-50 p-4 text-xs text-slate-700">{preview.text}</pre>{file.size > MAX_TEXT_PREVIEW && <p className="mt-2 text-xs text-slate-500">仅展示前 128 KiB；下载文件查看全文。</p>}</div> : <p role="status">正在读取文字预览…</p>
  return <p className="text-sm text-slate-500">此格式无法在上传前安全预览；请核对文件名，保存后查看平台解析结果。</p>
}
