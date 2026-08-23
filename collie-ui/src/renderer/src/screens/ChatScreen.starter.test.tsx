// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AttachmentDraft, CollieEvent, Conversation, TaskState } from '../lib/ipc'

interface ChatInputProps {
  onSend: (text: string, attachments: AttachmentDraft[]) => Promise<boolean>
  onProjectChange: (path: string) => void
  taskProgress?: TaskState | null
}

const hooks = vi.hoisted(() => {
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
    listener: (): ((event: CollieEvent) => void) => {
      if (!listener) throw new Error('ChatScreen did not register its event listener.')
      return listener
    }
  }
})

vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))
vi.mock('../components/Sidebar', () => ({ default: () => <aside /> }))
vi.mock('../components/ChatInput', () => ({
  default: () => <form />
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

  beforeEach(async () => {
    ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true
    localStorage.clear()
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
})
