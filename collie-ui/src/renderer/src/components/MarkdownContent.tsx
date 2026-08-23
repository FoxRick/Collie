import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { safeImageSource } from '../lib/safeImageSource'

interface Props {
  content: string
}

export default function MarkdownContent({ content }: Props): React.JSX.Element {
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url, key, node) => (
          key === 'src' && node.tagName === 'img'
            ? (safeImageSource(url) ?? '')
            : defaultUrlTransform(url)
        )}
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
          img: ({ src, alt }) => {
            const safeSource = safeImageSource(src)
            return safeSource ? (
              <img src={safeSource} alt={alt ?? ''} loading="lazy" />
            ) : (
              <span className="message-markdown-remote-image" role="note">
                Remote image hidden for privacy{alt ? `: ${alt}` : '.'}
              </span>
            )
          }
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
