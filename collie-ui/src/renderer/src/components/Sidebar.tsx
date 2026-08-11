import { useEffect, useRef, useState } from 'react'
import {
  Bot,
  Folder,
  FolderPlus,
  MessageCircle,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  PinOff,
  Plug,
  Repeat2,
  Search,
  Settings,
  Shapes,
  Trash2
} from 'lucide-react'
import { collieClient, type Conversation } from '../lib/ipc'
import { useT } from '../lib/i18n'
import {
  PINNED_CONVERSATIONS_STORAGE_KEY,
  SIDEBAR_COLLAPSED_STORAGE_KEY,
  readPinnedConversationIds,
  readSidebarCollapsed,
  reconcilePinnedConversationIds,
  type AppView
} from '../lib/navigation'
import CollieFace from './CollieFace'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  busyIds?: Set<string>
  activeView: AppView
  onNavigate: (view: AppView) => void
  onSelect: (id: string) => void
  onNewChat: () => void
  onDelete: (id: string) => void
  workspace?: string
  projects?: string[]
  onProjectChange: (path: string) => void
  onAddProject: () => void
}

export default function Sidebar({
  conversations,
  activeId,
  busyIds,
  activeView,
  onNavigate,
  onSelect,
  onNewChat,
  onDelete,
  workspace,
  projects = [],
  onProjectChange,
  onAddProject
}: Props): React.JSX.Element {
  const [query, setQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<boolean>(() =>
    typeof localStorage === 'undefined' ? false : readSidebarCollapsed(localStorage)
  )
  const [pinnedIds, setPinnedIds] = useState<string[]>(() =>
    typeof localStorage === 'undefined' ? [] : readPinnedConversationIds(localStorage)
  )
  const [contentMatches, setContentMatches] = useState<Set<string> | null>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const searchToggleRef = useRef<HTMLButtonElement>(null)
  const searchGenerationRef = useRef(0)
  const t = useT()

  const persistPinnedIds = (ids: string[]): void => {
    setPinnedIds(ids)
    localStorage.setItem(PINNED_CONVERSATIONS_STORAGE_KEY, JSON.stringify(ids))
  }

  const toggleCollapsed = (): void => {
    setSearchOpen(false)
    // Write storage from the updater's next value, not from `collapsed` in
    // this closure: the Ctrl+B handler in the mount-only effect below holds
    // the FIRST render's toggleCollapsed, so a stale `collapsed` read here
    // would persist the wrong value on every second keyboard toggle.
    setCollapsed((collapsedNow) => {
      const next = !collapsedNow
      localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, next ? '1' : '0')
      return next
    })
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'b') {
        event.preventDefault()
        toggleCollapsed()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    // Conversation data starts empty while the local store loads. Explicit deletes
    // are cleaned up below; wait for a populated snapshot before pruning old ids.
    if (conversations.length === 0) return
    const reconciled = reconcilePinnedConversationIds(
      pinnedIds,
      conversations.map((conversation) => conversation.id)
    )
    if (reconciled.length !== pinnedIds.length) persistPinnedIds(reconciled)
  }, [conversations, pinnedIds])

  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus()
  }, [searchOpen])

  useEffect(() => {
    const generation = ++searchGenerationRef.current
    const trimmed = query.trim()
    if (!trimmed) {
      setContentMatches(null)
      return
    }
    const timer = setTimeout(() => {
      void collieClient
        .searchMessages(trimmed)
        .then((data) => {
          if (searchGenerationRef.current === generation) {
            setContentMatches(new Set(data.results.map((m) => m.conversation_id)))
          }
        })
        .catch(() => {
          if (searchGenerationRef.current === generation) setContentMatches(new Set())
        })
    }, 250)
    return () => {
      clearTimeout(timer)
      if (searchGenerationRef.current === generation) searchGenerationRef.current += 1
    }
  }, [query])

  const trimmed = query.trim().toLowerCase()
  const visible = trimmed
    ? conversations.filter(
        (c) =>
          (c.title || '').toLowerCase().includes(trimmed) ||
          (contentMatches?.has(c.id) ?? false)
      )
    : conversations
  const pinnedIdSet = new Set(pinnedIds)
  const pinnedConversations = visible.filter((conversation) => pinnedIdSet.has(conversation.id))

  const primaryItems = [
    { key: 'agents' as const, label: t('sidebar.agents'), icon: Bot },
    { key: 'skills' as const, label: t('sidebar.skills'), icon: Shapes },
    { key: 'loops' as const, label: t('sidebar.routines'), icon: Repeat2 },
    { key: 'connectors' as const, label: t('sidebar.connectors'), icon: Plug }
  ]
  const generalConversations = visible
    .filter((conversation) => !conversation.project_path && !pinnedIdSet.has(conversation.id))
    .slice(0, 8)
  const projectConversations = (path: string): Conversation[] =>
    visible
      .filter(
        (conversation) =>
          conversation.project_path === path && !pinnedIdSet.has(conversation.id)
      )
      .slice(0, 5)

  const togglePinned = (id: string): void => {
    persistPinnedIds(
      pinnedIdSet.has(id) ? pinnedIds.filter((pinnedId) => pinnedId !== id) : [...pinnedIds, id]
    )
  }

  const deleteConversation = (id: string): void => {
    if (pinnedIdSet.has(id)) persistPinnedIds(pinnedIds.filter((pinnedId) => pinnedId !== id))
    onDelete(id)
  }

  const conversationRows = (items: Conversation[]): React.JSX.Element[] =>
    items.map((conv) => (
      <div
        key={conv.id}
        className={`conversation-row group flex items-center ${conv.id === activeId ? 'is-active' : ''}`}
      >
        <button
          onClick={() => onSelect(conv.id)}
          className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left text-sm"
          title={conv.title}
          aria-current={conv.id === activeId ? 'page' : undefined}
        >
          {busyIds?.has(conv.id) ? (
            <span
              className="h-2 w-2 shrink-0 animate-pulse rounded-full"
              role="status"
              aria-label={t('sidebar.working')}
              title={t('sidebar.working')}
              style={{ background: 'var(--collie-amber)' }}
            />
          ) : null}
          <span className="truncate">{conv.title || 'New chat'}</span>
        </button>
        <button
          type="button"
          onClick={() => togglePinned(conv.id)}
          className={`conversation-action rounded p-1 ${pinnedIdSet.has(conv.id) ? 'is-pinned' : ''}`}
          title={`${pinnedIdSet.has(conv.id) ? 'Unpin' : 'Pin'} chat: ${conv.title || 'New chat'}`}
          aria-label={`${pinnedIdSet.has(conv.id) ? 'Unpin' : 'Pin'} chat: ${conv.title || 'New chat'}`}
          aria-pressed={pinnedIdSet.has(conv.id)}
        >
          {pinnedIdSet.has(conv.id) ? <PinOff size={14} /> : <Pin size={14} />}
        </button>
        <button
          type="button"
          onClick={() => deleteConversation(conv.id)}
          className="conversation-action mr-1 rounded p-1"
          title={t('sidebar.delete', { title: conv.title || 'New chat' })}
          aria-label={t('sidebar.delete', { title: conv.title || 'New chat' })}
          style={{ color: 'var(--collie-text-sidebar-muted)' }}
        >
          <Trash2 size={14} />
        </button>
      </div>
    ))

  return (
    <aside className={`sidebar flex shrink-0 flex-col ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="brand-lockup">
        <div className="brand-mark"><CollieFace size={25} /></div>
        <div className="brand-name">Collie</div>
        <button
          ref={searchToggleRef}
          type="button"
          className={`sidebar-search-toggle ${searchOpen ? 'is-active' : ''}`}
          onClick={() =>
            setSearchOpen((open) => {
              if (open) setQuery('')
              return !open
            })
          }
          title={t('sidebar.searchLabel')}
          aria-label={t('sidebar.searchLabel')}
          aria-expanded={searchOpen}
          aria-controls="sidebar-search"
        >
          <Search size={17} />
        </button>
      </div>

      <button
        type="button"
        onClick={onNewChat}
        className="new-chat-button mx-4 mb-3 flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-semibold transition"
        title={t('sidebar.newChat')}
      >
        <MessageSquarePlus size={16} />
        <span>{t('sidebar.newChat')}</span>
      </button>

      <div
        id="sidebar-search"
        className="sidebar-search mx-4 mb-3 flex items-center gap-2 px-3 py-2"
        hidden={!searchOpen}
      >
          <Search size={13} aria-hidden="true" />
          <input
            ref={searchInputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setQuery('')
                setSearchOpen(false)
                requestAnimationFrame(() => searchToggleRef.current?.focus())
              }
            }}
            placeholder={t('sidebar.searchPlaceholder')}
            aria-label={t('sidebar.searchLabel')}
            className="w-full bg-transparent text-xs outline-none"
          />
      </div>

      <nav className="sidebar-primary px-3" aria-label="Primary navigation">
        {primaryItems.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => onNavigate(key)}
            className={`sidebar-nav-item ${activeView === key ? 'is-active' : ''}`}
            title={label}
            aria-current={activeView === key ? 'page' : undefined}
          >
            <Icon size={17} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <nav className="sidebar-conversation-nav flex-1 overflow-y-auto px-3" aria-label="Conversations">
        {conversations.length > 0 && visible.length === 0 && (
          <p className="px-2 py-6 text-center text-xs" style={{ color: 'var(--collie-text-sidebar-muted)' }}>
            {t('sidebar.noMatches')}
          </p>
        )}
        {pinnedConversations.length > 0 ? (
          <section className="sidebar-chat-group" aria-labelledby="sidebar-pinned-label">
            <div id="sidebar-pinned-label" className="sidebar-nested-label">Pinned</div>
            <div className="sidebar-nested-conversations">
              {conversationRows(pinnedConversations)}
            </div>
          </section>
        ) : null}
        <section className="sidebar-chat-group">
          <button
            type="button"
            className={`sidebar-context-row ${!workspace && activeView === 'chat' ? 'is-active' : ''}`}
            onClick={() => onProjectChange('')}
            aria-current={!workspace && activeView === 'chat' ? 'page' : undefined}
          >
            <MessageCircle size={15} />
            <span>General Chat</span>
          </button>
          <div className="sidebar-nested-label">Recent chats</div>
          <div className="sidebar-nested-conversations">
            {conversationRows(generalConversations)}
            {conversations.length === 0 ? (
              <p className="sidebar-empty-copy">{t('sidebar.empty')}</p>
            ) : null}
          </div>
        </section>

        <section className="sidebar-project">
          <div className="sidebar-project-heading">
            <div className="sidebar-section-label">Projects</div>
            <button type="button" onClick={onAddProject}>
              <FolderPlus size={12} /> New project
            </button>
          </div>
          <div className="sidebar-project-list">
            {projects.map((path) => {
              const recent = projectConversations(path)
              return (
                <div className="sidebar-project-group" key={path}>
                  <button
                    type="button"
                    className={`project-row ${path === workspace ? 'is-active' : ''}`}
                    title={path}
                    onClick={() => onProjectChange(path)}
                  >
                    <Folder size={13} />
                    <span>
                      <b>{path.split(/[\\/]/).filter(Boolean).at(-1) || path}</b>
                      <small>{path}</small>
                    </span>
                  </button>
                  {recent.length > 0 ? (
                    <div className="sidebar-nested-conversations">
                      {conversationRows(recent)}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        </section>
      </nav>

      <div className="sidebar-footer">
        <button
          type="button"
          onClick={toggleCollapsed}
          className="sidebar-collapse-toggle mx-4 mb-2 grid h-9 w-9 place-items-center transition"
          title={collapsed ? t('sidebar.expandNav') : t('sidebar.collapseNav')}
          aria-label={collapsed ? t('sidebar.expandNav') : t('sidebar.collapseNav')}
          aria-expanded={!collapsed}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
        <button
          onClick={() => onNavigate('settings')}
          className={`sidebar-settings sidebar-footer-row mx-4 mb-4 flex items-center gap-2 px-3 py-2.5 text-sm transition ${activeView === 'settings' ? 'is-active' : ''}`}
          title={t('sidebar.settings')}
          aria-current={activeView === 'settings' ? 'page' : undefined}
        >
          <Settings size={16} />
          <span>{t('sidebar.settings')}</span>
        </button>
      </div>
    </aside>
  )
}
