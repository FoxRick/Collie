import { useEffect, useState } from 'react'
import {
  Bot,
  Folder,
  FolderPlus,
  MessageCircle,
  MessageSquarePlus,
  Plug,
  Repeat2,
  Search,
  Settings,
  Shapes,
  Trash2
} from 'lucide-react'
import { collieClient, type Conversation } from '../lib/ipc'
import { useT } from '../lib/i18n'
import type { AppView } from '../lib/navigation'
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
  const [contentMatches, setContentMatches] = useState<Set<string> | null>(null)
  const t = useT()

  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setContentMatches(null)
      return
    }
    const timer = setTimeout(() => {
      void collieClient
        .searchMessages(trimmed)
        .then((data) =>
          setContentMatches(new Set(data.results.map((m) => m.conversation_id)))
        )
        .catch(() => setContentMatches(new Set()))
    }, 250)
    return () => clearTimeout(timer)
  }, [query])

  const trimmed = query.trim().toLowerCase()
  const visible = trimmed
    ? conversations.filter(
        (c) =>
          (c.title || '').toLowerCase().includes(trimmed) ||
          (contentMatches?.has(c.id) ?? false)
      )
    : conversations

  const primaryItems = [
    { key: 'agents' as const, label: t('sidebar.agents'), icon: Bot },
    { key: 'skills' as const, label: t('sidebar.skills'), icon: Shapes },
    { key: 'loops' as const, label: t('sidebar.routines'), icon: Repeat2 },
    { key: 'connectors' as const, label: t('sidebar.connectors'), icon: Plug }
  ]
  const generalConversations = visible
    .filter((conversation) => !conversation.project_path)
    .slice(0, 8)
  const projectConversations = (path: string): Conversation[] =>
    visible.filter((conversation) => conversation.project_path === path).slice(0, 5)

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
          onClick={() => onDelete(conv.id)}
          className="mr-1 rounded p-1 opacity-0 focus-visible:opacity-100 group-focus-within:opacity-100 group-hover:opacity-100"
          title={t('sidebar.delete', { title: conv.title || 'New chat' })}
          aria-label={t('sidebar.delete', { title: conv.title || 'New chat' })}
          style={{ color: 'var(--collie-text-sidebar-muted)' }}
        >
          <Trash2 size={14} />
        </button>
      </div>
    ))

  return (
    <aside className="sidebar flex w-72 shrink-0 flex-col">
      <div className="brand-lockup">
        <div className="brand-mark"><CollieFace size={25} /></div>
        <div className="brand-name">Collie</div>
      </div>

      <nav className="sidebar-primary px-3" aria-label="Primary navigation">
        {primaryItems.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => onNavigate(key)}
            className={`sidebar-nav-item ${activeView === key ? 'is-active' : ''}`}
            aria-current={activeView === key ? 'page' : undefined}
          >
            <Icon size={17} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-search mx-4 mb-3 mt-4 flex items-center gap-2 px-3 py-2">
        <Search size={13} style={{ color: 'var(--collie-text-sidebar-muted)' }} />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('sidebar.searchPlaceholder')}
          aria-label={t('sidebar.searchLabel')}
          className="w-full bg-transparent text-xs outline-none"
        />
      </div>

      <nav className="sidebar-conversation-nav flex-1 overflow-y-auto px-3" aria-label="Conversations">
        {conversations.length > 0 && visible.length === 0 && (
          <p className="px-2 py-6 text-center text-xs" style={{ color: 'var(--collie-text-sidebar-muted)' }}>
            {t('sidebar.noMatches')}
          </p>
        )}
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

      <button
        onClick={onNewChat}
        className="new-chat-button mx-4 mb-2 mt-3 flex items-center justify-center gap-2 px-3 py-2.5 text-sm font-semibold transition"
      >
        <MessageSquarePlus size={16} />
        {t('sidebar.newChat')}
      </button>
      <button
        onClick={() => onNavigate('settings')}
        className={`sidebar-settings mx-4 mb-4 flex items-center gap-2 px-3 py-2.5 text-sm transition ${activeView === 'settings' ? 'is-active' : ''}`}
        aria-current={activeView === 'settings' ? 'page' : undefined}
      >
        <Settings size={16} />
        {t('sidebar.settings')}
      </button>
    </aside>
  )
}
