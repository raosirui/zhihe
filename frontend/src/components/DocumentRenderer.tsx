import { lazy, Suspense, useMemo } from 'react'
import DOMPurify from 'dompurify'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { OriginalDownload } from './OriginalDownload'
import { lessonRawPath } from './protectedDocumentFetch'

const PdfReader = lazy(() => import('./PdfReader'))
const PptReader = lazy(() => import('./PptReader'))

type Props = {
  contentType?: string
  content?: string | null
  convertedHtml?: string | null
  rawFileUrl?: string | null
}

function SemanticDocument({ content, convertedHtml, contentType, rawFileUrl, download = false }: Props & { download?: boolean }) {
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
    {download && <OriginalDownload rawFileUrl={rawFileUrl} contentType={contentType} />}
  </div>
}

export function DocumentRenderer({ contentType, content, convertedHtml, rawFileUrl }: Props) {
  const fallback = <SemanticDocument content={content} convertedHtml={convertedHtml} contentType={contentType} />
  if (rawFileUrl && lessonRawPath(rawFileUrl)) {
    if (contentType === 'pdf') return <Suspense fallback={<p role="status">正在准备 PDF 阅读器…</p>}>
      <PdfReader rawFileUrl={rawFileUrl} fallback={fallback} hasText={Boolean(convertedHtml || (content && !content.startsWith('[Document:')))} />
    </Suspense>
    if (contentType === 'ppt') return <Suspense fallback={<p role="status">正在准备演示文稿…</p>}>
      <PptReader rawFileUrl={rawFileUrl} fallback={fallback} />
    </Suspense>
  }
  return <SemanticDocument contentType={contentType} content={content} convertedHtml={convertedHtml} rawFileUrl={rawFileUrl} download />
}
