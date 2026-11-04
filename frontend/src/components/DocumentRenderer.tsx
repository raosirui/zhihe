import ReactMarkdown from "react-markdown"

type Props = {
  contentType?: string
  content?: string | null
  convertedHtml?: string | null
  rawFileUrl?: string | null
}

export function DocumentRenderer({ contentType, content, convertedHtml, rawFileUrl }: Props) {
  switch (contentType) {
    case "word":
    case "html":
    case "richtext":
      if (convertedHtml) {
        return <div className="document-content prose max-w-none" dangerouslySetInnerHTML={{ __html: convertedHtml }} />
      }
      break
    case "pdf":
      if (rawFileUrl) {
        return (
          <div className="document-content">
            <iframe
              src={rawFileUrl}
              className="w-full h-[80vh] border-0"
              title="PDF Viewer"
            />
            <div className="mt-4 text-center">
              <a
                href={rawFileUrl}
                download
                className="inline-block px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                下载 PDF
              </a>
            </div>
          </div>
        )
      }
      break
    case "ppt":
      if (convertedHtml) {
        return (
          <div className="document-content">
            <style>{`
              .ppt-slide {
                background: white;
                border: 1px solid #e5e7eb;
                border-radius: 0.5rem;
                padding: 2rem;
                margin-bottom: 1.5rem;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
              }
              .ppt-slide h3 {
                color: #374151;
                font-size: 1.25rem;
                font-weight: 600;
                margin-bottom: 1rem;
                padding-bottom: 0.5rem;
                border-bottom: 2px solid #e5e7eb;
              }
            `}</style>
            <div dangerouslySetInnerHTML={{ __html: convertedHtml }} />
          </div>
        )
      }
      break
    case "markdown":
    default:
      if (content) {
        return (
          <div className="prose max-w-none">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )
      }
      break
  }

  // Fallback
  if (content) {
    return <div className="prose max-w-none">{content}</div>
  }
  return <div className="text-gray-500">暂无内容</div>
}
