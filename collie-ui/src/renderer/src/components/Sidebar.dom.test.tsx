// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Conversation } from '../lib/ipc'
import { PINNED_CONVERSATIONS_STORAGE_KEY, SIDEBAR_COLLAPSED_STORAGE_KEY } from '../lib/navigation'
import Sidebar from './Sidebar'

const { searchMessages } = vi.hoisted(() => ({
  searchMessages: vi.fn()
}))

vi.mock('lucide-react', () => ({
  Bot: () => null,
  ChevronDown: () => null,
  Folder: () => null,
  FolderPlus: () => null,
  MessageCircle: () => null,
  MessageSquarePlus: () => null,
  PanelLeftClose: () => null,
  PanelLeftOpen: () => null,
  Pin: () => null,
  PinOff: () => null,
  Plug: () => null,
  Repeat2: () => null,
  Search: () => null,
  Settings: () => null,
  Shapes: () => null,
  Trash2: () => null
}))
vi.mock('../lib/i18n', () => ({
  useT: () => (key: string): string => key
}))
vi.mock('../lib/ipc', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/ipc')>()
  return {
    ...original,
    collieClient: { searchMessages }
  }
})
vi.mock('./CollieFace', () => ({ default: () => null }))

const conversations: Conversation[] = [
  {
    id: 'recent',
    title: 'Recent chat',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    archived: 0
  },
  {
    id: 'pinned',
    title: 'Pinned chat',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    archived: 0
  },
  {
    id: 'project-chat',
    title: 'Project chat',
    project_path: '/home/rick/proj-a',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    archived: 0
  },
  {
    id: 'project-chat-b',
    title: 'Project chat B',
    project_path: '/home/rick/proj-b',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    archived: 0
  }
]

const roots: Root[] = []

interface SidebarTestOptions {
  onDelete?: (id: string) => void
  projects?: string[]
  workspace?: string
  onProjectChange?: (path: string) => void
}

function renderSidebar(options: SidebarTestOptions = {}): HTMLElement {
  const { onDelete = vi.fn(), projects = [], workspace, onProjectChange = vi.fn() } = options
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => {
    root.render(
      <Sidebar
        conversations={conversations}
        activeId={null}
        activeView="chat"
        onNavigate={vi.fn()}
        onSelect={vi.fn()}
        onNewChat={vi.fn()}
        onDelete={onDelete}
        workspace={workspace}
        projects={projects}
        onProjectChange={onProjectChange}
        onAddProject={vi.fn()}
      />
    )
  })
  return container
}

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function (): void { this.setAttribute('open', '') }
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  localStorage.clear()
  searchMessages.mockReset()
  searchMessages.mockResolvedValue({ results: [] })
})

it('opens feedback from the collapsed sidebar and returns focus on cancel', () => {
  localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, '1')
  const container = renderSidebar()
  const button = container.querySelector<HTMLButtonElement>('[aria-label="Submit Feedback"]')!
  expect(container.querySelector('aside')!.classList.contains('is-collapsed')).toBe(true)
  act(() => { button.focus(); button.click() })
  expect(document.querySelector('dialog[open]')).not.toBeNull()
  const cancel = Array.from(document.querySelectorAll('dialog button')).find((item) => item.textContent === 'Cancel') as HTMLButtonElement
  act(() => cancel.click())
  expect(document.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(button)
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Sidebar navigation', () => {
  it('puts New chat before the feature navigation and opens focused search from the header', () => {
    const container = renderSidebar()
    const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
    const newChat = buttons.find((button) => button.textContent === 'sidebar.newChat')!
    const agents = buttons.find((button) => button.textContent === 'sidebar.agents')!
    expect(buttons.indexOf(newChat)).toBeLessThan(buttons.indexOf(agents))

    const searchToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.searchLabel"]'
    )!
    const searchPanel = container.querySelector<HTMLElement>('#sidebar-search')!
    expect(searchPanel.hidden).toBe(true)
    act(() => searchToggle.click())
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="sidebar.searchLabel"]'
    )!
    expect(input).toBe(document.activeElement)
    expect(searchToggle.getAttribute('aria-expanded')).toBe('true')

    let restoreFocus: FrameRequestCallback = () => undefined
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn((callback: FrameRequestCallback) => {
        restoreFocus = callback
        return 1
      })
    )
    act(() => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    act(() => restoreFocus(0))
    expect(document.activeElement).toBe(searchToggle)
    expect(searchToggle.getAttribute('aria-expanded')).toBe('false')
    expect(searchPanel.hidden).toBe(true)

    act(() => searchToggle.click())
    const reopenedInput = container.querySelector<HTMLInputElement>(
      'input[aria-label="sidebar.searchLabel"]'
    )!

    act(() => {
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value'
      )?.set
      valueSetter?.call(reopenedInput, 'not in any chat')
      reopenedInput.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(
      Array.from(container.querySelectorAll('button')).some(
        (button) => button.textContent === 'Recent chat'
      )
    ).toBe(false)

    act(() => searchToggle.click())
    expect(searchPanel.hidden).toBe(true)
    expect(searchToggle.getAttribute('aria-expanded')).toBe('false')
    expect(
      Array.from(container.querySelectorAll('button')).some(
        (button) => button.textContent === 'Recent chat'
      )
    ).toBe(true)
  })

  it('shows persisted pins above recent chats and removes a pin when deleting', () => {
    localStorage.setItem(PINNED_CONVERSATIONS_STORAGE_KEY, JSON.stringify(['pinned']))
    const onDelete = vi.fn()
    const container = renderSidebar({ onDelete })
    const labels = Array.from(container.querySelectorAll('.sidebar-nested-label'))
    expect(labels.map((label) => label.textContent)).toEqual(['Pinned', 'Recent chats'])
    expect(labels[0]?.parentElement?.textContent).toContain('Pinned chat')
    expect(labels[1]?.parentElement?.textContent).not.toContain('Pinned chat')

    const deletePinned = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.delete"]'
    )!
    act(() => deletePinned.click())
    expect(onDelete).toHaveBeenCalledWith('pinned')
    expect(localStorage.getItem(PINNED_CONVERSATIONS_STORAGE_KEY)).toBe('[]')
  })

  it('ignores an older search response that resolves after the latest query', async () => {
    vi.useFakeTimers()
    let resolveFirst: (value: { results: Array<{ conversation_id: string }> }) => void =
      () => undefined
    let resolveSecond: (value: { results: Array<{ conversation_id: string }> }) => void =
      () => undefined
    searchMessages
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecond = resolve
          })
      )
    const container = renderSidebar()
    const searchToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.searchLabel"]'
    )!
    act(() => searchToggle.click())
    const input = container.querySelector<HTMLInputElement>(
      'input[aria-label="sidebar.searchLabel"]'
    )!
    const setInput = (value: string): void => {
      const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value'
      )?.set
      valueSetter?.call(input, value)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    }

    act(() => {
      setInput('first query')
      vi.advanceTimersByTime(250)
    })
    act(() => {
      setInput('second query')
      vi.advanceTimersByTime(250)
    })

    await act(async () => {
      resolveSecond({ results: [{ conversation_id: 'recent' }] })
      await Promise.resolve()
    })
    expect(container.querySelector('button[title="Recent chat"]')).not.toBeNull()
    expect(container.querySelector('button[title="Pinned chat"]')).toBeNull()

    await act(async () => {
      resolveFirst({ results: [{ conversation_id: 'pinned' }] })
      await Promise.resolve()
    })
    expect(container.querySelector('button[title="Recent chat"]')).not.toBeNull()
    expect(container.querySelector('button[title="Pinned chat"]')).toBeNull()
  })
})

describe('Sidebar layout', () => {
  it('keeps General Chat outside the scrolling conversation list', () => {
    const container = renderSidebar()
    const conversationNav = container.querySelector<HTMLElement>(
      'nav[aria-label="Conversations"]'
    )!
    const generalChat = container.querySelector<HTMLButtonElement>(
      '.sidebar-context-wrap button'
    )!
    expect(generalChat.textContent).toBe('General Chat')
    expect(conversationNav.contains(generalChat)).toBe(false)
  })

  it('shows project chats collapsed behind a toggle and expands on click', () => {
    const container = renderSidebar({
      projects: ['/home/rick/proj-a', '/home/rick/proj-b'],
      workspace: '/home/rick/proj-a'
    })
    const projectGroups = container.querySelectorAll<HTMLElement>('.sidebar-project-group')
    expect(projectGroups.length).toBe(2)

    const activeToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Collapse proj-a"]'
    )!
    expect(activeToggle.getAttribute('aria-expanded')).toBe('true')
    const collapsedToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Expand proj-b"]'
    )!
    expect(collapsedToggle.getAttribute('aria-expanded')).toBe('false')

    // Only the active project's chats are visible; the other stays folded.
    expect(container.querySelector('button[title="Project chat"]')).not.toBeNull()

    act(() => collapsedToggle.click())
    expect(collapsedToggle.getAttribute('aria-expanded')).toBe('true')

    act(() => collapsedToggle.click())
    expect(collapsedToggle.getAttribute('aria-expanded')).toBe('false')
    expect(container.querySelector('button[title="Project chat"]')).not.toBeNull()
  })

  it('selecting a project navigates and expands its chats', () => {
    const onProjectChange = vi.fn()
    const container = renderSidebar({
      projects: ['/home/rick/proj-b'],
      workspace: '/home/rick/proj-a',
      onProjectChange
    })
    const row = container.querySelector<HTMLButtonElement>('button[title="/home/rick/proj-b"]')!
    act(() => row.click())
    expect(onProjectChange).toHaveBeenCalledWith('/home/rick/proj-b')
    const toggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Collapse proj-b"]'
    )!
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })
})

describe('Sidebar collapse', () => {
  it('starts expanded, collapses on toggle, and persists the choice', () => {
    const container = renderSidebar()
    const aside = container.querySelector<HTMLElement>('aside')!
    expect(aside.classList.contains('is-collapsed')).toBe(false)

    const toggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.collapseNav"]'
    )!
    act(() => toggle.click())

    expect(aside.classList.contains('is-collapsed')).toBe(true)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-label')).toBe('sidebar.expandNav')
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('1')
  })

  it('renders collapsed when the preference is persisted, and expands on toggle', () => {
    localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, '1')
    const container = renderSidebar()
    const aside = container.querySelector<HTMLElement>('aside')!
    expect(aside.classList.contains('is-collapsed')).toBe(true)

    const toggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.expandNav"]'
    )!
    act(() => toggle.click())

    expect(aside.classList.contains('is-collapsed')).toBe(false)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('0')
  })

  it('toggles via Ctrl+B and closes any open search', () => {
    const container = renderSidebar()
    const searchToggle = container.querySelector<HTMLButtonElement>(
      'button[aria-label="sidebar.searchLabel"]'
    )!
    act(() => searchToggle.click())
    const searchPanel = container.querySelector<HTMLElement>('#sidebar-search')!
    expect(searchPanel.hidden).toBe(false)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', ctrlKey: true, bubbles: true }))
    })
    expect(container.querySelector('aside')!.classList.contains('is-collapsed')).toBe(true)
    expect(searchPanel.hidden).toBe(true)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('1')

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'B', ctrlKey: true, bubbles: true }))
    })
    expect(container.querySelector('aside')!.classList.contains('is-collapsed')).toBe(false)
    // The mount-only keydown handler must not persist a stale closure value:
    // after expanding via Ctrl+B the storage must say '0' (expanded), or a
    // reload would bring the collapsed rail back.
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('0')

    // And one more toggle cycle: collapse again, then expand — storage must
    // follow the UI state every time, not flip only on the first press.
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', ctrlKey: true, bubbles: true }))
    })
    expect(container.querySelector('aside')!.classList.contains('is-collapsed')).toBe(true)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('1')
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', ctrlKey: true, bubbles: true }))
    })
    expect(container.querySelector('aside')!.classList.contains('is-collapsed')).toBe(false)
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe('0')
  })
})
