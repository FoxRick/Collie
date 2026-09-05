import { memo, useLayoutEffect, useRef, useState } from 'react'
import type { CollieMessage } from '../lib/ipc'
import { useT } from '../lib/i18n'
import MessageBubble from './MessageBubble'
import { stableMarkdownStreamText, visibleStreamText } from '../lib/stream'

interface Props {
  messages: CollieMessage[]
  streamText: string
  streaming?: boolean
  activityLabel?: string
  cardPreview?: { card_type: string; card_data: Record<string, unknown> } | null
}

// Long conversations render in windows so the DOM stays light (Step 49).
const WINDOW_SIZE = 100

function MessageList({ messages, streamText, streaming = false, activityLabel, cardPreview }: Props): React.JSX.Element {
  const endRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldFollowRef = useRef(true)
  const [visibleCount, setVisibleCount] = useState(WINDOW_SIZE)
  const [following, setFollowing] = useState(true)
  const lastId = messages.length > 0 ? messages[messages.length - 1].id : ''
  const t = useT()

  useLayoutEffect(() => {
    if (shouldFollowRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
    }
  }, [lastId, messages, streamText, streaming, cardPreview])

  const visibleStream = stableMarkdownStreamText(visibleStreamText(streamText))
  const hidden = Math.max(0, messages.length - visibleCount)
  const visible = hidden > 0 ? messages.slice(hidden) : messages

  return (
    <div
      ref={scrollRef}
      className="chat-transcript flex-1 overflow-y-auto px-4 py-4"
      tabIndex={0}
      aria-label="Chat history"
      onWheel={(event) => {
        // Pause before the next text paint, not after its resulting scroll event.
        if (event.deltaY < 0) {
          shouldFollowRef.current = false
          setFollowing(false)
        }
      }}
      onKeyDown={(event) => {
        if (['ArrowUp', 'PageUp', 'Home'].includes(event.key)) {
          shouldFollowRef.current = false
          setFollowing(false)
        }
      }}
      onScroll={() => {
        const element = scrollRef.current
        if (!element) return
        shouldFollowRef.current =
          element.scrollHeight - element.scrollTop - element.clientHeight <= 1
        setFollowing(shouldFollowRef.current)
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
        {(visibleStream || cardPreview) && (
          <MessageBubble
            role="assistant"
            content={visibleStream}
            streaming={streaming}
            settled={false}
            cardType={cardPreview?.card_type ?? null}
            cardData={cardPreview?.card_data ?? null}
          />
        )}
        {streaming && !visibleStream && (
          <div className="chat-activity collie-thinking" role="status">
            <span className="collie-thinking-dots" aria-hidden="true"><i /><i /><i /></span>
            <span>{activityLabel || t('chat.thinking')}</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {!following && (
        <button type="button" className="chat-jump-latest" onClick={() => {
          shouldFollowRef.current = true
          setFollowing(true)
          endRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
        }}>↓ Jump to latest</button>
      )}
    </div>
  )
}

export default memo(MessageList)
