import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
}

/**
 * Only images that never hit the network are allowed into chat:
 *  - `data:` URIs (self-contained, used by tools/connectors for generated media)
 *  - relative / same-origin paths (the local media server, e.g. `/api/media/...`)
 * Anything with a remote scheme (`http:`, `https:`, `file:`, …) is blocked:
 * loading it would leak the user's IP and let a third-party server track them.
 */
function isSafeImageSrc(src: string | undefined): boolean {
  if (!src) return false
  if (src.startsWith('data:')) return true
  // A leading `scheme:` means remote or otherwise non-local — block it.
  // Relative paths have no scheme and stay local, so they pass.
  return !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(src)
}

export default function MarkdownContent({ content }: Props): React.JSX.Element {
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                if (!href) return
                event.preventDefault()
                void window.collie?.openExternal(href)
              }}
            >
              {children}
            </a>
          ),
          img: ({ src, alt, title }) => {
            if (!isSafeImageSrc(src)) return null
            return <img src={src} alt={alt ?? ''} title={title} />
          }
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}