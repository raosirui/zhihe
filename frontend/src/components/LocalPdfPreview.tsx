import { useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import 'react-pdf/dist/Page/TextLayer.css'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const pdfAssets = `${import.meta.env.BASE_URL}pdfjs/`
const options = { cMapUrl: `${pdfAssets}cmaps/`, cMapPacked: true, standardFontDataUrl: `${pdfAssets}standard_fonts/` }

export default function LocalPdfPreview({ url }: { url: string }) {
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  return <div aria-label="PDF 预览" className="max-w-full overflow-auto">
    <div className="mb-3 flex items-center gap-3 text-sm">
      <button type="button" disabled={page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>上一页</button>
      <span>{total ? `${page} / ${total}` : '正在载入…'}</span>
      <button type="button" disabled={page >= total} onClick={() => setPage(value => Math.min(total, value + 1))}>下一页</button>
    </div>
    <Document key={url} file={url} options={options} suspense={false} onLoadSuccess={({ numPages }) => { setTotal(numPages); setPage(1) }}
      loading={<p role="status">正在解析 PDF…</p>} error={<p role="alert">无法预览 PDF，可下载原始文件查看。</p>}>
      <Page pageNumber={page} width={Math.min(760, typeof window === 'undefined' ? 760 : window.innerWidth - 64)}
        renderAnnotationLayer={false} renderTextLayer suspense={false} />
    </Document>
  </div>
}
