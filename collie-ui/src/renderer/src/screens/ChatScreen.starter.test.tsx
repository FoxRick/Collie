// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DesktopAction } from '../../../shared/desktop-menu'
import type { AttachmentDraft, CollieEvent, Conversation } from '../lib/ipc'

interface InputProps {
  workspace: string
  onSend: (text: string, attachments: AttachmentDraft[]) => Promise<boolean>
}

const hooks = vi.hoisted(() => {
  let input: InputProps | undefined
  let listener: ((event: CollieEvent) => void) | undefined
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
    transcribe: vi.fn(),
    getStarterConversation: vi.fn()
  }
  return {
    client,
    captureInput: (props: InputProps) => { input = props },
    input: () => {
      if (!input) throw new Error('Chat input was not rendered')
      return input
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
  default: (props: InputProps) => { hooks.captureInput(props); return <form /> }
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
  default: () => <ol />
}))

import ChatScreen from './ChatScreen'

const starterConversation: Conversation = {
  id: 'starter-1',
  title: 'Getting started',
  created_at: '2026-08-05T00:00:00Z',
  updated_at: '2026-08-05T00:00:00Z',
  archived: 0
}

describe('ChatScreen starter-conversation opening', () => {
  let container: HTMLDivElement
  let root: Root
  let desktopAction: ((action: DesktopAction) => void) | undefined
  let desktopOff: ReturnType<typeof vi.fn>

  beforeEach(async () => {
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true
    localStorage.clear()
    desktopAction = undefined
    desktopOff = vi.fn()
    hooks.client.on.mockClear()
    hooks.client.chat.mockReset().mockResolvedValue({ conversation_id: 'c-1' })
    hooks.client.listConversations.mockReset().mockResolvedValue({ conversations: [] })
    hooks.client.getStatus.mockReset().mockResolvedValue({})
    hooks.client.listCommands.mockReset().mockResolvedValue({ commands: [], agents: [], skills: [] })
    hooks.client.listPendingApprovals.mockReset().mockResolvedValue({ approvals: [] })
    hooks.client.getMessages.mockReset().mockResolvedValue({ messages: [] })
    hooks.client.getActiveTask.mockReset().mockResolvedValue({ task: null })
    hooks.client.getStarterConversation
      .mockReset()
      .mockResolvedValue({ conversation: starterConversation, greeted: true })
    Object.defineProperty(window, 'collie', {
      configurable: true,
      value: {
        onDesktopAction: vi.fn((listener: (action: DesktopAction) => void) => {
          desktopAction = listener
          return desktopOff
        }),
        setDesktopMenu: vi.fn().mockResolvedValue(undefined),
        updateActiveWork: vi.fn(),
        petCommand: vi.fn(),
        pickProjectFolder: vi.fn()
      }
    })
    container = document.createElement('div')
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => root.unmount())
  })

  it('opens the starter conversation (seeded greeting) right after connect', async () => {
    await act(async () => {
      root.render(
        <ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} autoOpenStarter />
      )
      await Promise.resolve()
    })
    // The empty active conversation is offered as the seed target.
    expect(hooks.client.getStarterConversation).toHaveBeenCalledWith(null)
    // The returned conversation becomes the open one.
    expect(hooks.client.getMessages).toHaveBeenCalledWith('starter-1')
  })

  it('does not open the starter when the shell did not just connect', async () => {
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} />)
      await Promise.resolve()
    })
    expect(hooks.client.getStarterConversation).not.toHaveBeenCalled()
  })

  it('seeds the starter only once per mount (idempotent, no nagging)', async () => {
    await act(async () => {
      root.render(
        <ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} autoOpenStarter />
      )
      await Promise.resolve()
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(hooks.client.getStarterConversation).toHaveBeenCalledTimes(1)
  })
  it('routes tray actions through the normal conversation controls', async () => {
    const navigate = vi.fn()
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={navigate} onRedoOnboarding={vi.fn()} />)
    })
    await act(async () => desktopAction?.({ type: 'open-chat', conversationId: 'recent-1' }))
    expect(navigate).toHaveBeenLastCalledWith('chat')
    expect(hooks.client.getMessages).toHaveBeenCalledWith('recent-1')
    expect(window.collie.setDesktopMenu).toHaveBeenLastCalledWith({ conversations: [], activeId: 'recent-1' })
    await act(async () => desktopAction?.({ type: 'settings' }))
    expect(navigate).toHaveBeenLastCalledWith('settings')
    await act(async () => desktopAction?.({ type: 'new-chat' }))
    expect(navigate).toHaveBeenLastCalledWith('chat')
    expect(window.collie.setDesktopMenu).toHaveBeenLastCalledWith({ conversations: [], activeId: null })
  })

  it('refreshes recent chats on a new message and rename, without updating for streamed tokens', async () => {
    const navigate = vi.fn()
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={navigate} onRedoOnboarding={vi.fn()} />)
    })
    const conversation = { ...starterConversation, id: 'fresh-chat', title: 'New chat' }
    hooks.client.listConversations.mockResolvedValue({ conversations: [conversation] })
    await act(async () => hooks.listener()({
      type: 'message', conversation_id: conversation.id,
      message: { id: 'user-1', conversation_id: conversation.id, role: 'user', content: 'Hello',
        created_at: conversation.created_at }
    }))
    expect(window.collie.setDesktopMenu).toHaveBeenLastCalledWith({
      conversations: [{ id: 'fresh-chat', title: 'New chat' }], activeId: 'fresh-chat'
    })
    const updated = { ...conversation, title: 'Travel plans' }
    hooks.client.listConversations.mockResolvedValue({ conversations: [updated] })
    await act(async () => hooks.listener()({ type: 'conversation_updated', conversation: updated }))
    expect(window.collie.setDesktopMenu).toHaveBeenLastCalledWith({
      conversations: [{ id: 'fresh-chat', title: 'Travel plans' }], activeId: 'fresh-chat'
    })
    vi.mocked(window.collie.setDesktopMenu).mockClear()
    hooks.client.listConversations.mockClear()
    await act(async () => {
      for (let i = 0; i < 20; i++) hooks.listener()({ type: 'delta', conversation_id: conversation.id, text: 'word ' })
    })
    expect(window.collie.setDesktopMenu).not.toHaveBeenCalled()
    expect(hooks.client.listConversations).not.toHaveBeenCalled()
  })

  it('limits the tray list to the ten most recent sidebar conversations', async () => {
    const conversations = Array.from({ length: 12 }, (_, index) => ({
      ...starterConversation, id: `chat-${index}`, title: `Chat ${index}`
    }))
    hooks.client.listConversations.mockResolvedValue({ conversations })
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} />)
    })
    expect(window.collie.setDesktopMenu).toHaveBeenLastCalledWith({
      conversations: conversations.slice(0, 10).map(({ id, title }) => ({ id, title })), activeId: null
    })
  })

  it('switches project folders with tray selection and clears them for General Chat', async () => {
    const conversations = [
      { ...starterConversation, id: 'a', project_path: 'C:/project-a' },
      { ...starterConversation, id: 'b', project_path: 'C:/project-b' },
      { ...starterConversation, id: 'general', project_path: null }
    ]
    hooks.client.listConversations.mockResolvedValue({ conversations })
    await act(async () => {
      root.render(<ChatScreen activeView="chat" onNavigate={vi.fn()} onRedoOnboarding={vi.fn()} />)
    })
    await act(async () => desktopAction?.({ type: 'open-chat', conversationId: 'a' }))
    expect(hooks.input().workspace).toBe('C:/project-a')
    await act(async () => desktopAction?.({ type: 'open-chat', conversationId: 'b' }))
    expect(hooks.input().workspace).toBe('C:/project-b')
    hooks.client.chat.mockResolvedValue({ conversation_id: 'b' })
    await act(async () => { await hooks.input().onSend('Use B', []) })
    expect(hooks.client.chat).toHaveBeenLastCalledWith('b', 'Use B', [], 'execute',
      'C:/project-b', { mode: 'selected_folder' })
    await act(async () => desktopAction?.({ type: 'open-chat', conversationId: 'general' }))
    expect(hooks.input().workspace).toBe('')
    hooks.client.chat.mockResolvedValue({ conversation_id: 'general' })
    await act(async () => { await hooks.input().onSend('General', []) })
    expect(hooks.client.chat).toHaveBeenLastCalledWith('general', 'General', [], 'execute',
      undefined, { mode: 'selected_folder' })
  })

})
