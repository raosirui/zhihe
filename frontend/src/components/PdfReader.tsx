import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'react-pdf/dist/Page/TextLayer.css'
import { getSessionGeneration, getToken, sessionMatches } from '../shared/api'
import { fetchLessonBlob, lessonDocumentPath } from './protectedDocumentFetch'

// Keep the worker in the same bundle/dependency version as react-pdf's pdfjs import.
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const zoomSteps = [0.75, 1, 1.25, 1.5, 2]
// The Vite static-copy plugin serves these PDF.js assets locally in development
// and emits them beside the app in production. Never depend on an external CDN.
const pdfAssets = `${import.meta.env.BASE_URL}pdfjs/`
const pdfOptions = {
  cMapUrl: `${pdfAssets}cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `${pdfAssets}standard_fonts/`,
}
type Source = { path: string; generation: number; token: string | null; url: string }

// Continuous scrolling is capped for very large documents: every mounted
// page keeps a full canvas in memory, and office previews are normally
// well below this bound.
const MAX_SCROLL_PAGES = 120
const SCROLL_MODE_KEY = '***'
const readStoredMode = () => { try { return localStorage.getItem(SCROLL_MODE_KEY) === '1' } catch { return false } }

function ScrollPage({ pageNumber, width, ratio, onRatio, onError }: {
  pageNumber: number, width: number, ratio: number, onRatio: (value: number) => void, onError: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(pageNumber <= 2)
  useEffect(() => {
    if (mounted) return
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') { setMounted(true); return }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setMounted(true); observer.disconnect() }
    }, { rootMargin: '800px 0px' })
    observer.observe(el)
    return () => observer.disconnect()
  }, [mounted])
  // Unmounted pages keep an estimated height so the scrollbar stays usable;
  // the first page's real dimensions correct the estimate for the rest.
  return <div className="pdf-scroll-page" ref={ref} data-page-number={pageNumber}
    style={mounted ? undefined : { minHeight: Math.max(1, Math.round(width * ratio)) }}>
    {mounted && <Page
      pageNumber={pageNumber}
      width={width}
      renderAnnotationLayer={false}
      renderTextLayer
      suspense={false}
      loading={<p role="status">页面渲染中…</p>}
      onLoadError={onError}
      onRenderError={onError}
      onLoadSuccess={(page: { width?: number, height?: number }) => {
        const value = page.width && page.height ? page.height / page.width : 0
        if (pageNumber === 1 && value > 0.2 && value < 4) onRatio(value)
      }}
    />}
  </div>
}

export default function PdfReader({ rawFileUrl, fallback }: { rawFileUrl: string; fallback: ReactNode }) {
  const path = lessonDocumentPath(rawFileUrl)
  const pageContainer = useRef<HTMLDivElement>(null)
  const [revision, setRevision] = useState(getSessionGeneration)
  const [source, setSource] = useState<Source | null>(null)
  const [error, setError] = useState<{ path: string; generation: number; message: string } | null>(null)
  const [layoutFallback, setLayoutFallback] = useState<{ path: string; generation: number } | null>(null)
  const [pageNumber, setPageNumber] = useState(1), [totalPages, setTotalPages] = useState(0)
  const [zoomIndex, setZoomIndex] = useState(1), [availableWidth, setAvailableWidth] = useState(720)
  const [scroll, setScroll] = useState(readStoredMode)
  const [pageRatio, setPageRatio] = useState(1.414)

  useEffect(() => {
    const sessionChanged = () => setRevision(getSessionGeneration())
    window.addEventListener('zhihe:session', sessionChanged)
    return () => window.removeEventListener('zhihe:session', sessionChanged)
  }, [])
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
      }).catch((failure: unknown) => {
        if (active && sessionMatches(generation, token)) {
          if ((failure as { layoutUnavailable?: boolean })?.layoutUnavailable) setLayoutFallback({ path, generation })
          else setError({ path, generation, message: 'PDF 加载失败，请重新登录或稍后再试。' })
        }
      })
    }
    return () => { active = false; request.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [path, rawFileUrl, revision])

  const current = source?.path === path && sessionMatches(source.generation, source.token) ? source : null
  const currentError = error?.path === path && error.generation === getSessionGeneration() ? error.message : ''
  const failed = Boolean(currentError)

  // In continuous mode the page counter follows whichever page crosses the
  // viewport midline, so 上一页/下一页 stay meaningful while free-scrolling.
  useEffect(() => {
    if (!scroll || !current || failed) return
    let frame = 0
    const update = () => {
      frame = 0
      const root = pageContainer.current
      if (!root) return
      const midline = window.innerHeight / 2
      const nodes = Array.from(root.querySelectorAll<HTMLElement>('[data-page-number]'))
      if (!nodes.length) return
      let active = Number(nodes[0].dataset.pageNumber)
      for (const node of nodes) {
        active = Number(node.dataset.pageNumber)
        if (node.getBoundingClientRect().bottom >= midline) break
      }
      setPageNumber(previous => previous === active ? previous : active)
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update) }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [scroll, current, failed])

  const toggleScroll = () => {
    setScroll(value => {
      const next = !value
      try { localStorage.setItem(SCROLL_MODE_KEY, next ? '1' : '0') } catch { /* private mode */ }
      return next
    })
  }
  const goToPage = (target: number) => {
    setPageNumber(target)
    if (scroll) {
      pageContainer.current?.querySelector(`[data-page-number="${target}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  // Derived layout preview unavailable (422/404): show the sanitized text.
  if (path && !current && !failed && layoutFallback?.path === path && sessionMatches(layoutFallback.generation, getToken()))
    return <>{fallback}</>
  const previewFailed = () => {
    if (current && sessionMatches(current.generation, current.token)) {
      setError({ path: current.path, generation: current.generation, message: '无法预览 PDF。' })
    }
  }
  const pageWidth = Math.max(1, Math.round(availableWidth * zoomSteps[zoomIndex]))
  const scrollDisabled = !current || totalPages > MAX_SCROLL_PAGES
  // react-pdf 11 suspends by default; suspending inside the lazy boundary
  // unmounts this component and revokes its blob URL before parsing finishes.
  return <section className="pdf-reader" aria-label="PDF 文档阅读器">
    <div className="pdf-toolbar">
      <div className="pdf-actions">
        <button type="button" onClick={() => goToPage(Math.max(1, pageNumber - 1))} disabled={!totalPages || pageNumber === 1}>上一页</button>
        <span aria-live="polite">{totalPages ? `${pageNumber} / ${totalPages}` : '— / —'}</span>
        <button type="button" onClick={() => goToPage(Math.min(totalPages, pageNumber + 1))} disabled={!totalPages || pageNumber >= totalPages}>下一页</button>
        <button type="button" aria-label="缩小 PDF" onClick={() => setZoomIndex(n => Math.max(0, n - 1))} disabled={!current || zoomIndex === 0}>－</button>
        <span aria-live="polite">{Math.round(zoomSteps[zoomIndex] * 100)}%</span>
        <button type="button" aria-label="放大 PDF" onClick={() => setZoomIndex(n => Math.min(zoomSteps.length - 1, n + 1))} disabled={!current || zoomIndex === zoomSteps.length - 1}>＋</button>
        <button type="button" aria-pressed={scroll} disabled={scrollDisabled} onClick={toggleScroll}
          title={totalPages > MAX_SCROLL_PAGES ? `文档超过 ${MAX_SCROLL_PAGES} 页，连续滚动不可用` : '所有页面纵向排列，向下滚动连续阅读'}>
          连续滚动
        </button>
      </div>
    </div>
    <div className={scroll ? 'pdf-page pdf-scroll' : 'pdf-page'} ref={pageContainer}>
      {failed ? <div className="pdf-fallback" role="alert"><p>{currentError}</p>{fallback}</div> : !current ? (
        <p role="status">PDF 加载中…</p>
      ) : <Document
        key={`${current.url}:${scroll ? 'scroll' : 'single'}`}
        file={current.url}
        options={pdfOptions}
        suspense={false}
        loading={<p role="status">正在解析 PDF…</p>}
        onLoadSuccess={({ numPages }) => { if (sessionMatches(current.generation, current.token)) { setTotalPages(numPages); setPageNumber(1) } }}
        onLoadError={previewFailed}
        onSourceError={previewFailed}
      >{scroll
        ? Array.from({ length: totalPages }, (_, index) => <ScrollPage key={index + 1} pageNumber={index + 1}
            width={pageWidth} ratio={pageRatio} onRatio={setPageRatio} onError={previewFailed} />)
        : <Page
          pageNumber={pageNumber}
          width={pageWidth}
          renderAnnotationLayer={false}
          renderTextLayer
          suspense={false}
          loading={<p role="status">页面渲染中…</p>}
          onLoadError={previewFailed}
          onRenderError={previewFailed}
        />}</Document>}
    </div>
  </section>
}
