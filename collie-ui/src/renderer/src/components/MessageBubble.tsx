import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Image, X } from 'lucide-react'
import CardRenderer from './cards/CardRenderer'
import TakeawayCard from './cards/TakeawayCard'
import type { MessageAttachment, TaskState } from '../lib/ipc'
import MarkdownContent from './MarkdownContent'
import { visibleStreamText } from '../lib/stream'
import { buildTakeawayDigest } from '../lib/takeaway'
import TaskProgress, { isTaskTerminal } from './tasks/TaskProgress'

interface Props {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  /** False for the in-flight streaming bubble; the takeaway closer only
   * appears once the answer is complete and committed. */
  settled?: boolean
  cardType?: string | null
  cardData?: Record<string, unknown> | null
  attachments?: MessageAttachment[] | null
  taskState?: TaskState | null
}

const PREVIEWABLE_IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif'
])

function attachmentPreviewSource(attachment: MessageAttachment): string | null {
  if (!PREVIEWABLE_IMAGE_TYPES.has(attachment.mime)) return null
  const source = attachment.preview_data_url || ''
  return /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source) ? source : null
}

function MessageBubble({ role, content, streaming, settled = true, cardType, cardData, attachments, taskState }: Props): React.JSX.Element {
  const [preview, setPreview] = useState<{ name: string; source: string } | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const returnFocusRef = useRef<HTMLButtonElement | null>(null)
  const isUser = role === 'user'
  const visibleContent = isUser ? content : visibleStreamText(content)
  const takeaway = useMemo(() => buildTakeawayDigest(content), [content])

  const closePreview = useCallback((): void => {
    setPreview(null)
    window.setTimeout(() => returnFocusRef.current?.focus(), 0)
  }, [])

  useEffect(() => {
    if (!preview) return
    closeButtonRef.current?.focus()
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') closePreview()
      if (event.key === 'Tab') {
        event.preventDefault()
        closeButtonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [closePreview, preview])

  return (
    <div
      className={`message-row ${streaming ? 'message-row--streaming' : 'collie-reveal'} ${isUser ? 'message-row--user' : 'message-row--assistant'}`}
    >
      <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div
          className="message-bubble max-w-[80%] whitespace-pre-wrap px-4 py-3 text-[15px] leading-relaxed"
        >
          {attachments && attachments.length > 0 && (
            <div className="message-attachments">
              {attachments.map((attachment, index) => {
                const source = attachmentPreviewSource(attachment)
                return source ? (
                  <button
                    type="button"
                    className="message-attachment message-attachment--image"
                    key={`${attachment.name}-${index}`}
                    aria-label={`Open ${attachment.name} preview`}
                    onClick={(event) => {
                      returnFocusRef.current = event.currentTarget
                      setPreview({ name: attachment.name, source })
                    }}
                  >
                    <img src={source} alt="" />
                    <span>{attachment.name}</span>
                  </button>
                ) : (
                  <span className="message-attachment" key={`${attachment.name}-${index}`}>
                    {attachment.mime.startsWith('image/') ? <Image size={14} /> : <FileText size={14} />}
                    {attachment.name}
                  </span>
                )
              })}
            </div>
          )}
          {visibleContent && (
            isUser || streaming
              ? <span className="whitespace-pre-wrap">{visibleContent}</span>
              : <MarkdownContent content={visibleContent} />
          )}
          {streaming && <span className="collie-thinking">▍</span>}
        </div>
      </div>
      {!isUser && cardType && cardData && (
        <div className="flex justify-start collie-reveal mt-1">
          <CardRenderer cardType={cardType} cardData={cardData} />
        </div>
      )}
      {!isUser && taskState && isTaskTerminal(taskState) ? (
        <div className="flex justify-start collie-reveal mt-1">
          <TaskProgress task={taskState} readOnly />
        </div>
      ) : null}
      {!isUser && settled && !streaming && takeaway && (
        <div className="flex justify-start collie-reveal mt-1">
          <TakeawayCard digest={takeaway} />
        </div>
      )}
      {preview ? (
        <div
          className="attachment-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`${preview.name} preview`}
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) closePreview()
          }}
        >
          <div className="attachment-lightbox-card">
            <button
              ref={closeButtonRef}
              type="button"
              aria-label="Close image preview"
              onClick={closePreview}
            >
              <X size={18} />
            </button>
            <img src={preview.source} alt={preview.name} />
            <p>{preview.name}</p>
          </div>
        </div>
      ) : null}
    </div>
  )
}

// Bubbles are pure render targets — memoize so long chats don't re-render
// every bubble on each streamed delta (Step 49).
export default memo(MessageBubble)
