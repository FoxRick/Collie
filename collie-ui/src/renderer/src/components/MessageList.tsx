import { useEffect, useRef, useState } from 'react'
import type { CollieMessage } from '../lib/ipc'
import { useT } from '../lib/i18n'
import MessageBubble from './MessageBubble'
import { stableMarkdownStreamText, visibleStreamText } from '../lib/stream'

interface Props {
  messages: CollieMessage[]
  streamText: string
  cardPreview?: { card_type: string; card_data: Record<string, unknown> } | null
}

// Long conversations render in windows so the DOM stays light (Step 49).
const WINDOW_SIZE = 100

export default function MessageList({ messages, streamText, cardPreview }: Props): React.JSX.Element {
  const endRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldFollowRef = useRef(true)
  const [visibleCount, setVisibleCount] = useState(WINDOW_SIZE)
  const lastId = messages.length > 0 ? messages[messages.length - 1].id : ''
  const t = useT()

  useEffect(() => {
    if (shouldFollowRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [lastId])

  useEffect(() => {
    // Repeated smooth-scroll animations fight each other while text streams.
    // Following the paced reveal directly keeps the viewport visually stable.
    if (streamText && shouldFollowRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
    }
  }, [streamText])

  const visibleStream = stableMarkdownStreamText(visibleStreamText(streamText))
  const hidden = Math.max(0, messages.length - visibleCount)
  const visible = hidden > 0 ? messages.slice(hidden) : messages

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-4 py-4"
      onScroll={() => {
        const element = scrollRef.current
        if (!element) return
        shouldFollowRef.current =
          element.scrollHeight - element.scrollTop - element.clientHeight < 96
      }}
    >
      <div
        className="mx-auto flex max-w-3xl flex-col gap-3"
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        {hidden > 0 && (
          <button
            onClick={() => setVisibleCount((count) => count + WINDOW_SIZE)}
            className="mx-auto rounded-full border px-4 py-1.5 text-xs font-medium"
            style={{ borderColor: 'var(--collie-border)', color: 'var(--collie-text-muted)' }}
          >
            {t('chat.digUp', { count: Math.min(hidden, WINDOW_SIZE), hidden })}
          </button>
        )}
        {visible.map((msg) => (
          <MessageBubble
            key={msg.id}
            role={msg.role}
            content={msg.content}
            cardType={msg.card_type}
            cardData={msg.card_data}
            attachments={msg.attachments}
            taskState={msg.task_state}
          />
        ))}
        {visibleStream && <MessageBubble role="assistant" content={visibleStream} cardType={cardPreview?.card_type ?? null} cardData={cardPreview?.card_data ?? null} />}
        <div ref={endRef} />
      </div>
    </div>
  )
}
