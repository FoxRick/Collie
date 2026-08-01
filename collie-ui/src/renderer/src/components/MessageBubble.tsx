import { memo } from 'react'
import { FileText, Image } from 'lucide-react'
import CardRenderer from './cards/CardRenderer'
import type { MessageAttachment, TaskState } from '../lib/ipc'
import MarkdownContent from './MarkdownContent'
import { visibleStreamText } from '../lib/stream'
import TaskProgress, { isTaskTerminal } from './tasks/TaskProgress'

interface Props {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  cardType?: string | null
  cardData?: Record<string, unknown> | null
  attachments?: MessageAttachment[] | null
  taskState?: TaskState | null
}

function MessageBubble({ role, content, streaming, cardType, cardData, attachments, taskState }: Props): React.JSX.Element {
  const isUser = role === 'user'
  const visibleContent = isUser ? content : visibleStreamText(content)
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
              {attachments.map((attachment, index) => (
                <span key={`${attachment.name}-${index}`}>
                  {attachment.mime.startsWith('image/') ? <Image size={14} /> : <FileText size={14} />}
                  {attachment.name}
                </span>
              ))}
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
    </div>
  )
}

// Bubbles are pure render targets — memoize so long chats don't re-render
// every bubble on each streamed delta (Step 49).
export default memo(MessageBubble)
