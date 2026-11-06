import { useEffect, useRef, useState } from 'react'
import { downloadAuthed, getSessionGeneration, getToken, isAbort, sessionMatches } from '../shared/api'
import { lessonRawPath } from './protectedDocumentFetch'

const extensions: Record<string, string> = {
  pdf: 'pdf', ppt: 'pptx', word: 'docx', html: 'html', richtext: 'rtf', markdown: 'md',
}

export function OriginalDownload({ rawFileUrl, contentType, label = '下载原始文件' }: {
  rawFileUrl?: string | null
  contentType?: string
  label?: string
}) {
  const path = lessonRawPath(rawFileUrl)
  const current = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')

  useEffect(() => {
    setBusy(false); setError('')
    const cancel = () => { current.current?.abort(); current.current = null; setBusy(false); setError('') }
    window.addEventListener('zhihe:session', cancel)
    return () => { window.removeEventListener('zhihe:session', cancel); current.current?.abort(); current.current = null }
  }, [path])

  if (!path) return null
  const download = async () => {
    current.current?.abort()
    const request = new AbortController(), token = getToken(), generation = getSessionGeneration()
    current.current = request; setBusy(true); setError('')
    try {
      await downloadAuthed(path.slice('/api'.length), `课程原始文档.${extensions[contentType || ''] || 'bin'}`, {
        signal: request.signal,
        current: () => current.current === request && sessionMatches(generation, token),
      })
    } catch (failure) {
      if (current.current === request && !isAbort(failure)) setError((failure as Error).message || '下载失败')
    } finally {
      if (current.current === request) { current.current = null; setBusy(false) }
    }
  }
  return <span className="document-download-control">
    <button type="button" className="document-download" disabled={busy} onClick={() => void download()}>
      {busy ? '下载中…' : label}
    </button>
    {error && <span role="alert" className="text-sm text-red-700">{error}</span>}
  </span>
}
