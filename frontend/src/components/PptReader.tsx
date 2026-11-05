import { useEffect, useRef, useState, type ReactNode } from 'react'
import { getSessionGeneration, getToken, sessionMatches } from '../lib'
import { OriginalDownload } from './OriginalDownload'
import { fetchLessonPreview, lessonPreviewPath } from './protectedDocumentFetch'

type JsonObject = Record<string, unknown>
type Preview = { version: 1; width: number; height: number; slides: { shapes: JsonObject[] }[] }
type PreviewState = { path: string; generation: number; token: string | null; preview: Preview }

const isObject = (value: unknown): value is JsonObject => Boolean(value && typeof value === 'object' && !Array.isArray(value))
const dimension = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 20000 ? value : null
const validCoordinate = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 100000
const coordinate = (value: unknown, fallback = 0) => validCoordinate(value) ? value : fallback
const color = (value: unknown) => typeof value === 'string' && /^#[\da-f]{6}$/i.test(value) ? value : undefined
const lineWidth = (value: unknown) => Math.max(0, Math.min(20, coordinate(value, 0)))
const fontSize = (value: unknown) => Math.max(6, Math.min(180, coordinate(value, 18)))
const fontFamily = (value: unknown) => typeof value === 'string' && [
  'Arial', 'Helvetica', 'Times New Roman', 'Georgia', 'Courier New', 'Microsoft YaHei', '微软雅黑', '宋体', 'SimSun',
].includes(value) ? value : 'Arial, sans-serif'
const imageSource = (value: unknown) => typeof value === 'string' && value.length < 12_000_000 &&
  /^data:image\/(?:png|jpeg);base64,[a-z\d+/]+={0,2}$/i.test(value) ? value : undefined

function parsePreview(value: unknown): Preview | null {
  if (!isObject(value) || value.version !== 1) return null
  const width = dimension(value.width), height = dimension(value.height)
  if (!width || !height || !Array.isArray(value.slides) || !value.slides.length || value.slides.length > 40) return null
  const slides: Preview['slides'] = []
  let totalShapes = 0
  for (const slide of value.slides) {
    if (!isObject(slide) || !Array.isArray(slide.shapes) || (totalShapes += slide.shapes.length) > 500 ||
      !slide.shapes.every(shape => isObject(shape) && ['text', 'image', 'rect', 'ellipse', 'line', 'table'].includes(String(shape.type)) &&
        validCoordinate(shape.x) && validCoordinate(shape.y) && validCoordinate(shape.width) && validCoordinate(shape.height) &&
        shape.width >= 0 && shape.height >= 0 && (shape.type !== 'table' ||
          (Array.isArray(shape.rows) && Array.isArray(shape.colWidths) && Array.isArray(shape.rowHeights) &&
            shape.rows.length <= 100 && shape.colWidths.length <= 50 && shape.rowHeights.length <= 100)))) return null
    slides.push({ shapes: slide.shapes })
  }
  return { version: 1, width, height, slides }
}

// A readable transcript comes from the structured slide data, not from HTML scraping.
function slideText(slide: Preview['slides'][number]): string[] {
  const lines: string[] = []
  const append = (paragraphs: unknown) => {
    if (!Array.isArray(paragraphs)) return
    for (const paragraph of paragraphs.slice(0, 100)) {
      if (!isObject(paragraph) || !Array.isArray(paragraph.runs)) continue
      const line = paragraph.runs.slice(0, 200).map((run: unknown) => isObject(run) && typeof run.text === 'string' ? run.text : '').join('').trim()
      if (line && lines.length < 200) lines.push(line)
    }
  }
  for (const shape of slide.shapes) {
    if (lines.length >= 200) break
    if (shape.type === 'text') append(shape.paragraphs)
    if (shape.type === 'table' && Array.isArray(shape.rows)) {
      for (const row of shape.rows.slice(0, 100)) if (Array.isArray(row)) for (const cell of row.slice(0, 50)) if (isObject(cell)) append(cell.paragraphs)
    }
  }
  return lines
}

function Paragraphs({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return null
  return <>{value.slice(0, 100).map((paragraph: unknown, index: number) => {
    if (!isObject(paragraph) || !Array.isArray(paragraph.runs)) return null
    const align = ['left', 'center', 'right', 'justify'].includes(String(paragraph.align))
      ? paragraph.align as 'left' | 'center' | 'right' | 'justify' : 'left'
    return <p key={index} style={{ margin: 0, textAlign: align, whiteSpace: 'pre-wrap', lineHeight: 1.25 }}>
      {paragraph.runs.slice(0, 200).map((run: unknown, runIndex: number) => isObject(run) && typeof run.text === 'string' ?
        <span key={runIndex} style={{
          fontSize: fontSize(run.fontSize), fontFamily: fontFamily(run.fontFamily),
          color: color(run.color), fontWeight: run.bold === true ? 700 : undefined,
          fontStyle: run.italic === true ? 'italic' : undefined,
          textDecoration: run.underline === true ? 'underline' : undefined,
        }}>{run.text}</span> : null)}
    </p>
  })}</>
}

function SlideShape({ value, slideWidth, slideHeight }: { value: JsonObject; slideWidth: number; slideHeight: number }) {
  const { type } = value
  if (!['text', 'image', 'rect', 'ellipse', 'line', 'table'].includes(String(type))) return null
  const x = coordinate(value.x), y = coordinate(value.y)
  const width = Math.max(0, coordinate(value.width)), height = Math.max(0, coordinate(value.height))
  const borderColor = color(value.stroke), borderWidth = lineWidth(value.strokeWidth)
  if (type === 'line') {
    const x1 = coordinate(value.x1, x), y1 = coordinate(value.y1, y)
    const x2 = coordinate(value.x2, x + width), y2 = coordinate(value.y2, y + height)
    return <svg className="ppt-shape" aria-hidden="true" style={{ position: 'absolute', inset: 0, width: slideWidth, height: slideHeight, pointerEvents: 'none' }}
      viewBox={`0 0 ${slideWidth} ${slideHeight}`}><line x1={x1} y1={y1} x2={x2} y2={y2} stroke={borderColor || '#334155'} strokeWidth={borderWidth || 1} /></svg>
  }
  const shapeStyle = { position: 'absolute' as const, left: x, top: y, width, height, overflow: 'hidden' as const }
  if (type === 'image') {
    const src = imageSource(value.src)
    return src ? <img className="ppt-shape" src={src} alt="" style={{ ...shapeStyle, objectFit: 'contain' }} /> : null
  }
  if (type === 'table') {
    if (!Array.isArray(value.rows) || !Array.isArray(value.colWidths) || !Array.isArray(value.rowHeights)) return null
    const cols = value.colWidths.slice(0, 50).map((size: unknown) => Math.max(1, coordinate(size, 1)))
    const rows = value.rowHeights.slice(0, 100).map((size: unknown) => Math.max(1, coordinate(size, 1)))
    if (!cols.length || !rows.length) return null
    return <div className="ppt-shape ppt-table" style={{ ...shapeStyle, display: 'grid',
      gridTemplateColumns: cols.map(size => `${size}px`).join(' '), gridTemplateRows: rows.map(size => `${size}px`).join(' ') }}>
      {rows.flatMap((_, row) => cols.map((_, col) => {
        const tableRow = value.rows as unknown[]
        const cell = Array.isArray(tableRow[row]) ? (tableRow[row] as unknown[])[col] : undefined
        return <div key={`${row}-${col}`} className="ppt-table-cell" style={{ backgroundColor: isObject(cell) ? color(cell.fill) : undefined,
          border: '1px solid #cbd5e1', overflow: 'hidden', boxSizing: 'border-box' }}>
          {isObject(cell) && <Paragraphs value={cell.paragraphs} />}
        </div>
      }))}
    </div>
  }
  return <div className="ppt-shape" style={{ ...shapeStyle,
    backgroundColor: color(value.fill), border: borderColor && borderWidth ? `${borderWidth}px solid ${borderColor}` : undefined,
    borderRadius: type === 'ellipse' ? '50%' : undefined, boxSizing: 'border-box' }}>
    {type === 'text' && <Paragraphs value={value.paragraphs} />}
  </div>
}

function SlideCanvas({ preview, index, frame }: { preview: Preview; index: number; frame: React.RefObject<HTMLDivElement | null> }) {
  const [frameWidth, setFrameWidth] = useState(0)
  useEffect(() => {
    const element = frame.current
    if (!element) return
    const measure = () => { if (element.clientWidth > 0) setFrameWidth(element.clientWidth) }
    measure()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(measure)
      observer.observe(element)
      return () => observer.disconnect()
    }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [frame])
  const scale = (frameWidth || preview.width) / preview.width
  return <div ref={frame} className="ppt-stage" role="img" aria-label={`第 ${index + 1} 张幻灯片`}
    style={{ width: '100%', aspectRatio: `${preview.width} / ${preview.height}`, position: 'relative', overflow: 'hidden' }}>
    <div className="ppt-slide" style={{ position: 'absolute', left: 0, top: 0, width: preview.width, height: preview.height,
      backgroundColor: '#fff', transformOrigin: 'top left', transform: `scale(${scale})` }}>
      {preview.slides[index].shapes.map((shape, shapeIndex) => <SlideShape key={shapeIndex} value={shape} slideWidth={preview.width} slideHeight={preview.height} />)}
    </div>
  </div>
}

function SlideTranscript({ slide, index }: { slide: Preview['slides'][number]; index: number }) {
  const lines = slideText(slide)
  return <section className="ppt-transcript" aria-label={`第 ${index + 1} 张幻灯片文字`}>
    <h3>本页文字</h3>
    {lines.length ? lines.map((line, lineIndex) => <p key={lineIndex}>{line}</p>) :
      <p>本页没有可提取的文字；请查看图片或下载原始文件。</p>}
  </section>
}

export default function PptReader({ rawFileUrl, fallback }: { rawFileUrl: string; fallback: ReactNode }) {
  const path = lessonPreviewPath(rawFileUrl)
  const frame = useRef<HTMLDivElement>(null)
  const [revision, setRevision] = useState(getSessionGeneration)
  const [stored, setStored] = useState<PreviewState | null>(null)
  const [error, setError] = useState<{ path: string; generation: number; message: string } | null>(null)
  const [index, setIndex] = useState(0), [overview, setOverview] = useState(false)
  const [showTranscript, setShowTranscript] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches)

  useEffect(() => {
    const sessionChanged = () => setRevision(getSessionGeneration())
    window.addEventListener('zhihe:session', sessionChanged)
    return () => window.removeEventListener('zhihe:session', sessionChanged)
  }, [])
  useEffect(() => {
    const request = new AbortController(), generation = getSessionGeneration(), token = getToken()
    let active = true
    setStored(null); setError(null); setIndex(0); setOverview(false)
    setShowTranscript(typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches)
    if (path) {
      void fetchLessonPreview(rawFileUrl, request.signal).then(value => {
        if (!active || !sessionMatches(generation, token)) return
        const preview = parsePreview(value)
        if (!preview) throw new Error('演示文稿预览格式不受支持')
        setStored({ path, generation, token, preview })
      }).catch((failure: unknown) => {
        if (active && sessionMatches(generation, token)) {
          setError({ path, generation, message: failure instanceof Error ? failure.message : '预览暂不可用' })
        }
      })
    }
    return () => { active = false; request.abort() }
  }, [path, rawFileUrl, revision])

  const preview = stored?.path === path && sessionMatches(stored.generation, stored.token) ? stored.preview : null
  const currentError = error?.path === path && error.generation === getSessionGeneration() ? error.message : ''
  return <section className="ppt-reader" aria-label="演示文稿阅读器">
    <div className="ppt-toolbar"><strong>演示文稿 {preview ? `(${preview.slides.length} 张)` : ''}</strong>
      <div className="ppt-actions">
        {preview && <button type="button" aria-pressed={overview} onClick={() => setOverview(value => !value)}>{overview ? '单张阅读' : '查看全部'}</button>}
        <OriginalDownload rawFileUrl={rawFileUrl} contentType="ppt" label="下载 PPT" />
      </div>
    </div>
    {currentError || !path ? <div className="ppt-fallback" role="alert"><p>{currentError || '无法预览该演示文稿。'}</p>{fallback}</div>
      : !preview ? <p role="status">正在加载幻灯片…</p>
        : overview ? <div className="ppt-overview" aria-label="幻灯片目录">
          {preview.slides.map((slide, slideIndex) => <button type="button" key={slideIndex} onClick={() => { setIndex(slideIndex); setOverview(false) }}>
            <span>第 {slideIndex + 1} 张</span>
            <span className="ppt-overview-title">{slideText(slide).join(' ').slice(0, 90) || '幻灯片'}</span>
          </button>)}
        </div> : <>
          <SlideCanvas preview={preview} index={Math.min(index, preview.slides.length - 1)} frame={frame} />
          <nav className="ppt-navigation" aria-label="幻灯片翻页">
            <button type="button" disabled={index === 0} onClick={() => setIndex(value => Math.max(0, value - 1))}>上一张</button>
            <span aria-live="polite">{index + 1} / {preview.slides.length}</span>
            <button type="button" disabled={index >= preview.slides.length - 1} onClick={() => setIndex(value => Math.min(preview.slides.length - 1, value + 1))}>下一张</button>
            <button type="button" aria-expanded={showTranscript} onClick={() => setShowTranscript(value => !value)}>
              {showTranscript ? '收起本页文字' : '阅读本页文字'}
            </button>
          </nav>
          {showTranscript && <SlideTranscript slide={preview.slides[index]} index={index} />}
        </>}
  </section>
}
