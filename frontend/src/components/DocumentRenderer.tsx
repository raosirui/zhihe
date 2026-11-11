import { lazy, Suspense, useMemo } from 'react'
import DOMPurify from 'dompurify'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { lessonDocumentPath, lessonRawPath } from './protectedDocumentFetch'

const PdfReader = lazy(() => import('./PdfReader'))
const PptReader = lazy(() => import('./PptReader'))

type Props = {
  contentType?: string
  content?: string | null
  convertedHtml?: string | null
  rawFileUrl?: string | null
  pdfPreviewUrl?: string | null
}

function SemanticDocument({ content, convertedHtml, contentType }: Props) {
  // The server sanitizes conversions, but a client-side allowlist also protects
  // older documents and fallback HTML in case stored content predates that policy.
  const safeHtml = useMemo(() => convertedHtml ? DOMPurify.sanitize(convertedHtml, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['iframe', 'style', 'form', 'object', 'embed'],
    FORBID_ATTR: ['style'],
  }) : '', [convertedHtml])
  const preferHtml = Boolean(safeHtml) && (contentType !== 'markdown' || content?.startsWith('[Document:'))
  return <div className="document-content prose max-w-none">
    {preferHtml ? <div dangerouslySetInnerHTML={{ __html: safeHtml }} /> : content && !content.startsWith('[Document:') ?
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{content}</ReactMarkdown> :
      safeHtml ? <div dangerouslySetInnerHTML={{ __html: safeHtml }} /> : <p>暂无可预览的文档文字。</p>}
  </div>
}

export function DocumentRenderer({ contentType, content, convertedHtml, rawFileUrl, pdfPreviewUrl }: Props) {
  const semantic = <SemanticDocument content={content} convertedHtml={convertedHtml} contentType={contentType} />
  const rawOk = Boolean(rawFileUrl) && Boolean(lessonRawPath(rawFileUrl))
  // PPTX keeps its data-only slide reader as a degraded view (used when the
  // layout PDF is not advertised or cannot be rendered); text formats degrade
  // to the semantic view.
  const slideReader = contentType === 'ppt' && rawOk
    ? <Suspense fallback={<p role="status">正在准备演示文稿…</p>}><PptReader rawFileUrl={rawFileUrl!} fallback={semantic} /></Suspense>
    : semantic
  const previewOk = Boolean(pdfPreviewUrl) && Boolean(lessonDocumentPath(pdfPreviewUrl))
  const readerSource = contentType === 'pdf' && rawOk ? rawFileUrl!
    : (contentType === 'word' || contentType === 'ppt' || contentType === 'richtext') && previewOk ? pdfPreviewUrl! : null
  if (readerSource) return <Suspense fallback={<p role="status">正在准备 PDF 阅读器…</p>}>
    <PdfReader rawFileUrl={readerSource} fallback={contentType === 'ppt' ? slideReader : semantic} />
  </Suspense>
  return slideReader
}
