import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'react-pdf/dist/Page/TextLayer.css'
import { getSessionGeneration, getToken, sessionMatches } from '../shared/api'
import { fetchLessonBlob, lessonRawPath } from './protectedDocumentFetch'

// Keep the worker in the same bundle/dependency version as react-pdf's pdfjs import.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const zoomSteps = [0.75, 1, 1.25, 1.5, 2]
const onSmallScreen = () => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches
// The Vite static-copy plugin serves these PDF.js assets locally in development
// and emits them beside the app in production. Never depend on an external CDN.
const pdfAssets = `${import.meta.env.BASE_URL}pdfjs/`
const pdfOptions = {
  cMapUrl: `${pdfAssets}cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `${pdfAssets}standard_fonts/`,
}
type Source = { path: string; generation: number; token: string | null; url: string }

export default function PdfReader({ rawFileUrl, fallback, hasText }: { rawFileUrl: string; fallback: ReactNode; hasText: boolean }) {
  const path = lessonRawPath(rawFileUrl)
  const pageContainer = useRef<HTMLDivElement>(null)
  const [revision, setRevision] = useState(getSessionGeneration)
  const [source, setSource] = useState<Source | null>(null)
  const [error, setError] = useState<{ path: string; generation: number; message: string } | null>(null)
  const [pageNumber, setPageNumber] = useState(1), [totalPages, setTotalPages] = useState(0)
  const [zoomIndex, setZoomIndex] = useState(1), [availableWidth, setAvailableWidth] = useState(720)
  const [showText, setShowText] = useState(() => hasText && onSmallScreen())

  useEffect(() => {
    const sessionChanged = () => setRevision(getSessionGeneration())
    window.addEventListener('zhihe:session', sessionChanged)
    return () => window.removeEventListener('zhihe:session', sessionChanged)
  }, [])
  useEffect(() => { setShowText(hasText && onSmallScreen()) }, [path, revision, hasText])
  useEffect(() => {
    const container = pageContainer.current
    if (!container) return
    const measure = () => { if (container.clientWidth > 0) setAvailableWidth(container.clientWidth) }
    measure()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure)
      observer.observe(container)
      return () => observer.disconnect()
    }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  useEffect(() => {
    const request = new AbortController(), generation = getSessionGeneration(), token = getToken()
    let active = true, objectUrl: string | null = null
    setSource(null); setError(null); setPageNumber(1); setTotalPages(0); setZoomIndex(1)
    if (path) {
      void fetchLessonBlob(rawFileUrl, request.signal).then(blob => {
        if (!active || !sessionMatches(generation, token)) return
        objectUrl = URL.createObjectURL(blob)
        setSource({ path, generation, token, url: objectUrl })
      }).catch(() => {
        if (active && sessionMatches(generation, token)) {
          setError({ path, generation, message: 'PDF 加载失败，请重新登录或稍后再试。' })
        }
      })
    }
    return () => { active = false; request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [path, rawFileUrl, revision])

  const current = source?.path === path && sessionMatches(source.generation, source.token) ? source : null
  const currentError = error?.path === path && error.generation === getSessionGeneration() ? error.message : ''
  const failed = Boolean(currentError)
  const previewFailed = () => {
    if (current && sessionMatches(current.generation, current.token)) {
      setError({ path: current.path, generation: current.generation, message: '无法预览 PDF，可阅读以下提取文字。' })
    }
  }
  // react-pdf 11 suspends by default; suspending inside the lazy boundary
  // unmounts this component and revokes its blob URL before parsing finishes.
  return <section className="pdf-reader" aria-label="PDF 文档阅读器">
    <div className="pdf-toolbar">
      <strong>PDF 文档</strong>
      <div className="pdf-actions">
        <button type="button" onClick={() => setPageNumber(n => Math.max(1, n - 1))} disabled={!totalPages || pageNumber === 1}>上一页</button>
        <span aria-live="polite">{totalPages ? `${pageNumber} / ${totalPages}` : '— / —'}</span>
        <button type="button" onClick={() => setPageNumber(n => Math.min(totalPages, n + 1))} disabled={!totalPages || pageNumber >= totalPages}>下一页</button>
        <button type="button" aria-label="缩小 PDF" onClick={() => setZoomIndex(n => Math.max(0, n - 1))} disabled={!current || zoomIndex === 0}>－</button>
        <span aria-live="polite">{Math.round(zoomSteps[zoomIndex] * 100)}%</span>
        <button type="button" aria-label="放大 PDF" onClick={() => setZoomIndex(n => Math.min(zoomSteps.length - 1, n + 1))} disabled={!current || zoomIndex === zoomSteps.length - 1}>＋</button>
        {hasText && <button type="button" aria-expanded={showText} onClick={() => setShowText(value => !value)}>
          {showText ? '收起提取文字' : '阅读提取文字'}
        </button>}
      </div>
    </div>
    {hasText && showText && current && !failed && <section className="pdf-text" aria-label="PDF 提取文字">
      <h3>PDF 提取文字</h3>
      {fallback}
    </section>}
    <div className="pdf-page" ref={pageContainer}>
      {failed ? <div className="pdf-fallback" role="alert"><p>{currentError}</p>{fallback}</div> : !current ? (
        <p role="status">PDF 加载中…</p>
      ) : <Document
        key={current.url}
        file={current.url}
        options={pdfOptions}
        suspense={false}
        loading={<p role="status">正在解析 PDF…</p>}
        onLoadSuccess={({ numPages }) => { if (sessionMatches(current.generation, current.token)) { setTotalPages(numPages); setPageNumber(1) } }}
        onLoadError={previewFailed}
        onSourceError={previewFailed}
      ><Page
        pageNumber={pageNumber}
        width={Math.max(1, Math.round(availableWidth * zoomSteps[zoomIndex]))}
        renderAnnotationLayer={false}
        renderTextLayer
        suspense={false}
        loading={<p role="status">页面渲染中…</p>}
        onLoadError={previewFailed}
        onRenderError={previewFailed}
      /></Document>}
    </div>
  </section>
}
