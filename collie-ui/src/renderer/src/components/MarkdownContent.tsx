import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
}

function isLocalMarkdownImage(src: string | undefined): boolean {
  if (!src) return false
  const value = src.trim()
  if (/^data:image\/(?:gif|jpe?g|png|webp);base64,/i.test(value)) return true
  return !/^(?:[a-z][a-z\d+.-]*:|[\\/]{2})/i.test(value)
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
          img: ({ src, alt }) =>
            isLocalMarkdownImage(src) ? (
              <img src={src} alt={alt ?? ''} loading="lazy" />
            ) : (
              <span className="message-markdown-remote-image" role="note">
                Remote image hidden for privacy{alt ? `: ${alt}` : '.'}
              </span>
            )
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
