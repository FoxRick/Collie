// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AttachmentDraft, CollieEvent, CollieMessage } from '../lib/ipc'

interface ChatInputProps {
  onSend: (text: string, attachments: AttachmentDraft[]) => void
  onProjectChange: (path: string) => void
}

const hooks = vi.hoisted(() => {
  let listener: ((event: CollieEvent) => void) | undefined
  let chatInputProps: ChatInputProps | undefined
  const client = {
    on: vi.fn((next: (event: CollieEvent) => void) => {
      listener = next
      return () => {
        if (listener === next) listener = undefined
      }
    }),
    listConversations: vi.fn(),
    getStatus: vi.fn(),
    listCommands: vi.fn(),
    listPendingApprovals: vi.fn(),
    getMessages: vi.fn(),
    getActiveTask: vi.fn(),
    chat: vi.fn(),
    steerConversation: vi.fn(),
    setExecutionMode: vi.fn(),
    stopConversation: vi.fn(),
    deleteConversation: vi.fn(),
    activateProvider: vi.fn(),
    changePlan: vi.fn(),
    transcribe: vi.fn()
  }
  return {
    client,
    captureChatInput: (props: ChatInputProps): void => {
      chatInputProps = props
    },
    chatInput: (): ChatInputProps => {
      if (!chatInputProps) throw new Error('ChatScreen did not render ChatInput.')
      return chatInputProps
    },
    listener: (): ((event: CollieEvent) => void) => {
      if (!listener) throw new Error('ChatScreen did not register its event listener.')
      return listener
    }
  }
})

vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))
vi.mock('../components/Sidebar', () => ({ default: () => <aside /> }))
vi.mock('../components/ChatInput', () => ({
  default: (props: ChatInputProps) => {
    hooks.captureChatInput(props)
    return <form />
  }
}))
vi.mock('../components/CollieFace', () => ({ default: () => <span /> }))
vi.mock('../components/InteractiveColliePortrait', () => ({ default: () => <aside /> }))
vi.mock('../components/approvals/ApprovalSheet', () => ({ default: () => null }))
vi.mock('../components/tasks/TaskProgress', () => ({
  default: () => null,
  isTaskTerminal: () => false
}))
vi.mock('../components/plans/planChange', () => ({
  PLAN_CHANGE_REQUEST_EVENT: 'collie:change-plan',
  publishPlanChangeResult: vi.fn()
}))
vi.mock('./SettingsScreen', () => ({ default: () => null }))
vi.mock('./AgentsScreen', () => ({ default: () => null }))
vi.mock('./SkillsScreen', () => ({ default: () => null }))
vi.mock('./RoutinesScreen', () => ({ default: () => null }))
vi.mock('./ConnectorsScreen', () => ({ default: () => null }))
vi.mock('../components/MessageList', () => ({
  default: ({ messages }: { messages: CollieMessage[] }) => (
    <ol>
      {messages.map((message) => (
        <li data-message={`${message.role}:${message.id}`} key={message.id}>
          {message.content}
        </li>
      ))}
    </ol>
  )
}))

import ChatScreen from './ChatScreen'

const conversationId = 'conversation-1'
const message = (id: string, role: CollieMessage['role'], content: string): CollieMessage => ({
  id,
  conversation_id: conversationId,
  role,
  content,
  created_at: '2026-08-01T00:00:00Z'
})

describe('ChatScreen paced assistant completion order', () => {
  let container: HTMLDivElement
  let root: ReturnType<typeof createRoot>

  beforeEach(async () => {
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true
    vi.useFakeTimers()
    localStorage.clear()
    hooks.client.on.mockClear()
    hooks.client.chat.mockReset().mockResolvedValue({ conversation_id: conversationId })
    hooks.client.listConversations.mockResolvedValue({ conversations: [] })
    hooks.client.getStatus.mockResolvedValue({})
    hooks.client.listCommands.mockResolvedValue({ commands: [], agents: [], skills: [] })
    hooks.client.listPendingApprovals.mockResolvedValue({ approvals: [] })
    hooks.client.getMessages.mockResolvedValue({ messages: [] })
    hooks.client.getActiveTask.mockResolvedValue({ task: null })
    Object.defineProperty(window, 'collie', {
      configurable: true,
      value: {
        updateActiveWork: vi.fn(),
        petCommand: vi.fn(),
        pickProjectFolder: vi.fn()
      }
    })
    container = document.createElement('div')
    root = createRoot(container)
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} />)
      await Promise.resolve()
    })
  })

  afterEach(() => {
    act(() => root.unmount())
    vi.useRealTimers()
  })

  it('keeps a completed assistant turn above a later user turn while its text catches up', async () => {
    const emit = hooks.listener()
    const firstUser = message('user-1', 'user', 'Make a plan')
    const assistant = message('assistant-1', 'assistant', 'Here is the full, paced response.')
    const laterUser = message('user-2', 'user', 'Actually, make it shorter')

    await act(async () => {
      emit({ type: 'message', conversation_id: conversationId, message: firstUser })
      await Promise.resolve()
    })
    act(() => emit({ type: 'delta', conversation_id: conversationId, text: assistant.content }))
    act(() => emit({ type: 'message', conversation_id: conversationId, message: assistant }))
    act(() => emit({ type: 'message', conversation_id: conversationId, message: laterUser }))

    expect([...container.querySelectorAll<HTMLElement>('[data-message]')].map((item) => item.dataset.message)).toEqual([
      'user:user-1',
      'assistant:assistant-1',
      'user:user-2'
    ])

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(container.querySelector('[data-message="assistant:assistant-1"]')?.textContent).toBe(
      assistant.content
    )
  })

  it('sends an explicit selected-folder scope for General Chat', async () => {
    await act(async () => {
      hooks.chatInput().onSend('Hello', [])
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(hooks.client.chat).toHaveBeenCalledWith(
      null,
      'Hello',
      [],
      'execute',
      undefined,
      { mode: 'selected_folder' }
    )
  })

  it('starts a fresh General Chat instead of retaining an active project conversation scope', async () => {
    const emit = hooks.listener()
    await act(async () => {
      emit({ type: 'message', conversation_id: conversationId, message: message('user-1', 'user', 'Use a project') })
      await Promise.resolve()
    })

    await act(async () => {
      hooks.chatInput().onProjectChange('C:\\Project')
      await Promise.resolve()
    })
    await act(async () => {
      hooks.chatInput().onSend('Use the project', [])
      await Promise.resolve()
    })
    expect(hooks.client.chat).toHaveBeenLastCalledWith(
      conversationId,
      'Use the project',
      [],
      'execute',
      'C:\\Project',
      { mode: 'selected_folder' }
    )

    await act(async () => {
      hooks.chatInput().onProjectChange('')
      await Promise.resolve()
    })
    await act(async () => {
      hooks.chatInput().onSend('Start General Chat', [])
      await Promise.resolve()
    })
    expect(hooks.client.chat).toHaveBeenLastCalledWith(
      null,
      'Start General Chat',
      [],
      'execute',
      undefined,
      { mode: 'selected_folder' }
    )
  })
})
