import { memo, useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import type { CollaborationEvent } from '../lib/collaboration'

interface Props {
  events: CollaborationEvent[]
  readOnly?: boolean
  viewerId?: string
  onEdit?: (event: CollaborationEvent) => void
}

function SharedMessageList({
  events,
  readOnly,
  viewerId,
  onEdit
}: Props): React.JSX.Element {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [events.length])
  return (
    <div
      className="shared-events shared-events-reusable"
      role="log"
      aria-label="Shared conversation"
    >
      {events.length === 0 ? (
        <p className="settings-empty">No published messages yet.</p>
      ) : (
        events.map((event) => (
          <div key={event.id}>
            <MessageBubble
              role={event.role === 'system' ? 'assistant' : event.role}
              content={event.content}
              authorName={
                event.author_name ||
                (event.role === 'assistant' ? 'Collie' : 'Member')
              }
              syncStatus={event.sync_status}
            />
            {!readOnly &&
              event.author_id === viewerId &&
              event.role === 'user' &&
              event.sync_status === 'synced' &&
              onEdit && (
                <button
                  className="settings-button"
                  onClick={() => onEdit(event)}
                >
                  Edit or remove my message
                </button>
              )}
          </div>
        ))
      )}
      <div ref={endRef} />
    </div>
  )
}

export default memo(SharedMessageList)
