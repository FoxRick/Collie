import { useCallback, useEffect, useRef, useState } from 'react'
import {
  collieClient,
  type AttachmentDraft,
  type ApprovalRequest,
  type CollieEvent,
  type CollieMessage,
  type CommandCatalog,
  type Conversation,
  type ExecutionMode,
  type RuntimeStatus,
  type TaskState,
  type ThinkingState
} from '../lib/ipc'
import Sidebar from '../components/Sidebar'
import MessageList from '../components/MessageList'
import ChatInput from '../components/ChatInput'
import CollieFace from '../components/CollieFace'
import InteractiveColliePortrait from '../components/InteractiveColliePortrait'
import SettingsScreen from './SettingsScreen'
import AgentsScreen from './AgentsScreen'
import SkillsScreen from './SkillsScreen'
import RoutinesScreen from './RoutinesScreen'
import ConnectorsScreen from './ConnectorsScreen'
import type { AppView } from '../lib/navigation'
import { mergeStreamDelta } from '../lib/stream'
import ApprovalSheet from '../components/approvals/ApprovalSheet'
import TaskProgress, { isTaskTerminal } from '../components/tasks/TaskProgress'
import {
  applyTaskState,
  beginTaskHydration,
  isCurrentConversationEvent,
  isCurrentTaskHydration,
  recordTaskStateEvent
} from '../components/tasks/taskState'
import {
  PLAN_CHANGE_REQUEST_EVENT,
  publishPlanChangeResult,
  type PlanChangeRequest
} from '../components/plans/planChange'

interface Props {
  activeView: AppView
  onNavigate: (view: AppView) => void
  onRedoOnboarding: () => void
}

interface TaskTiming {
  startedAt: number
  completedMs?: number
}

function formatElapsed(milliseconds: number): string {
  const totalSeconds = Math.max(1, Math.floor(milliseconds / 1000))
  if (totalSeconds < 60) return `${totalSeconds}s`
  const totalMinutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (totalMinutes < 60) return seconds ? `${totalMinutes}m ${seconds}s` : `${totalMinutes}m`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

/** Runs its own 1 s tick so the chat screen does not re-render per second. */
function ElapsedLabel({ timing }: { timing: TaskTiming | undefined }): React.JSX.Element | null {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!timing || timing.completedMs !== undefined) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [timing])
  if (!timing) return null
  return (
    <span className="elapsed-label">
      {timing.completedMs !== undefined
        ? `Worked on this for ${formatElapsed(timing.completedMs)}`
        : `Working on this for ${formatElapsed(now - timing.startedAt)}`}
    </span>
  )
}

function loadRecentProjects(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem('collie.projects') || '[]')
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === 'string' && Boolean(item))
      : []
  } catch {
    return []
  }
}

export default function ChatScreen({
  activeView,
  onNavigate,
  onRedoOnboarding
}: Props): React.JSX.Element {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<CollieMessage[]>([])
  const [streamText, setStreamText] = useState('')
  const [thinkingMap, setThinkingMap] = useState<Record<string, ThinkingState>>({})
  const [errorText, setErrorText] = useState('')
  const [planChangeNotice, setPlanChangeNotice] = useState('')
  const [portraitThinking, setPortraitThinking] = useState<ThinkingState | null>(null)
  const [isTyping, setIsTyping] = useState(false)
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(() =>
    localStorage.getItem('collie.executionMode') === 'execute' ? 'execute' : 'plan'
  )
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([])
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus>({})
  const [commandCatalog, setCommandCatalog] = useState<CommandCatalog>({
    commands: [],
    agents: [],
    skills: []
  })
  const [projects, setProjects] = useState<string[]>(loadRecentProjects)
  const [currentProject, setCurrentProject] = useState('')
  const [taskTimings, setTaskTimings] = useState<Record<string, TaskTiming>>({})
  const [tasksByConversation, setTasksByConversation] = useState<Record<string, TaskState>>({})
  const [activeRoutineRuns, setActiveRoutineRuns] = useState<Set<string>>(new Set())
  const [activityReady, setActivityReady] = useState(false)
  const [cardPreview, setCardPreview] = useState<{ card_type: string; card_data: Record<string, unknown> } | null>(null)
  const activeIdRef = useRef<string | null>(null)
  const streamRef = useRef('')
  const rafRef = useRef(0)
  const loadTokenRef = useRef(0)
  const taskSnapshotGenerationRef = useRef<Record<string, number>>({})
  activeIdRef.current = activeId

  const refreshConversations = useCallback(async () => {
    try {
      const { conversations } = await collieClient.listConversations()
      setConversations(conversations)
    } catch {
      // core still starting; sidebar stays empty
    }
  }, [])

  const refreshRuntimeStatus = useCallback(async () => {
    try {
      setRuntimeStatus(await collieClient.getStatus())
    } catch {
      // The local core may still be starting.
    }
  }, [])

  /** Keep the latest complete snapshot; a late response must not regress it. */
  const applyTaskSnapshot = useCallback((conversationId: string, task: TaskState | null) => {
    setTasksByConversation((previous) => applyTaskState(previous, conversationId, task))
  }, [])

  const hydrateActiveTask = useCallback(async (conversationId: string) => {
    const generation = beginTaskHydration(taskSnapshotGenerationRef.current, conversationId)
    try {
      const { task } = await collieClient.getActiveTask(conversationId)
      // A slow read must not erase a pushed state that arrived after the request.
      if (
        activeIdRef.current === conversationId &&
        isCurrentTaskHydration(taskSnapshotGenerationRef.current, conversationId, generation)
      ) {
        applyTaskSnapshot(conversationId, task)
      }
    } catch {
      // The core may still be reconnecting; the next ready event retries.
    }
  }, [applyTaskSnapshot])

  const refreshCommandCatalog = useCallback(async () => {
    try {
      setCommandCatalog(await collieClient.listCommands())
    } catch {
      // Commands remain usable when typed even if the palette is still loading.
    }
  }, [])

  const rememberProject = useCallback((path: string) => {
    setCurrentProject(path)
    if (!path) return
    setProjects((current) => {
      const next = [path, ...current.filter((item) => item !== path)].slice(0, 8)
      localStorage.setItem('collie.projects', JSON.stringify(next))
      return next
    })
  }, [])

  const openConversation = useCallback(async (id: string | null, projectPath?: string | null) => {
    const token = ++loadTokenRef.current
    setActiveId(id)
    setStreamText('')
    setErrorText('')
    setPlanChangeNotice('')
    setPortraitThinking(null)
    setCardPreview(null)
    streamRef.current = ''
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }
    if (projectPath !== undefined) rememberProject(projectPath || '')
    if (!id) {
      if (token === loadTokenRef.current) setMessages([])
      return
    }
    try {
      const { messages } = await collieClient.getMessages(id)
      // A slow load must never overwrite a conversation switched to meanwhile.
      if (token === loadTokenRef.current) {
        setMessages(messages)
        void hydrateActiveTask(id)
      }
    } catch {
      if (token === loadTokenRef.current) setMessages([])
    }
  }, [hydrateActiveTask, rememberProject])

  useEffect(() => {
    void Promise.all([
      refreshConversations(),
      refreshRuntimeStatus(),
      refreshCommandCatalog(),
      collieClient
        .listPendingApprovals()
        .then((data) => setApprovals(data.approvals))
        .catch(() => undefined)
    ]).finally(() => setActivityReady(true))
  }, [refreshCommandCatalog, refreshConversations, refreshRuntimeStatus])

  // Apply a conversation's saved mode once per switch — never re-stomp the
  // user's live selection on every conversation-list refresh.
  const appliedModeForRef = useRef<string | null>(null)
  useEffect(() => {
    if (activeId === appliedModeForRef.current) return
    appliedModeForRef.current = activeId
    const conversation = conversations.find((item) => item.id === activeId)
    if (conversation?.execution_mode) setExecutionMode(conversation.execution_mode)
  }, [activeId, conversations])

  useEffect(() => {
    const off = collieClient.on((event: CollieEvent) => {
      const current = activeIdRef.current
      switch (event.type) {
        case 'ready':
          // The core (re)started: re-sync everything — conversations, status,
          // the open conversation's messages, and any stuck thinking state.
          void refreshConversations()
          void refreshRuntimeStatus()
          void refreshCommandCatalog()
          setThinkingMap({})
          if (current) {
            collieClient
              .getMessages(current)
              .then(({ messages }) => {
                if (activeIdRef.current === current) setMessages(messages)
              })
              .catch(() => undefined)
            void hydrateActiveTask(current)
          }
          break
        case 'connection_opened':
          if (current) void hydrateActiveTask(current)
          break
        case 'approval_requested':
          setApprovals((current) =>
            current.some((item) => item.id === event.approval.id)
              ? current
              : [...current, event.approval]
          )
          setPortraitThinking({
            state: 'waiting',
            phrase: 'Approval needed before I can continue.',
            pet_animation: 'concerned'
          })
          void window.collie?.petCommand(
            'status:concerned|Approval needed in Collie. Open the app to review.'
          )
          if (!document.hasFocus()) {
            try {
              if (
                Notification.permission === 'granted' ||
                Notification.permission === 'default'
              ) {
                const notification = new Notification('Collie needs your approval', {
                  body: 'Review the requested action so your task can continue.',
                  silent: false
                })
                notification.onclick = () => {
                  void window.collie?.showWindow()
                  onNavigate('chat')
                }
              }
            } catch {
              // The persistent pet bubble and in-app card remain available.
            }
          }
          break
        case 'approval_resolved':
          setApprovals((current) => current.filter((item) => item.id !== event.approval.id))
          void window.collie?.petCommand('status:dismiss')
          break
        case 'thinking': {
          const key = event.conversation_id || current
          if (!key) break
          const terminal = ['done', 'idle', 'error'].includes(event.state)
          setTaskTimings((previous) => {
            const existing = previous[key]
            if (terminal) {
              if (!existing || existing.completedMs !== undefined) return previous
              return {
                ...previous,
                [key]: {
                  ...existing,
                  completedMs: Math.max(0, Date.now() - existing.startedAt)
                }
              }
            }
            if (existing && existing.completedMs === undefined) return previous
            return { ...previous, [key]: { startedAt: Date.now() } }
          })
          setThinkingMap((prev) => {
            const next = { ...prev }
            if (terminal) {
              delete next[key]
            } else {
              next[key] = event
            }
            return next
          })
          if (event.conversation_id === current || !event.conversation_id) {
            setPortraitThinking(event)
            if (event.phrase && !document.hasFocus()) {
              void window.collie?.petCommand(
                `status:${event.pet_animation}|${event.phrase.slice(0, 110)}`
              )
            } else {
              void window.collie?.petCommand(event.pet_animation)
            }
          }
          break
        }
        case 'delta':
          if (event.conversation_id === current) {
            streamRef.current = mergeStreamDelta(streamRef.current, event.text)
            if (!rafRef.current) {
              rafRef.current = requestAnimationFrame(() => {
                setStreamText(streamRef.current)
                rafRef.current = 0
              })
            }
          }
          break
        case 'card':
          if (event.conversation_id === current) {
            setCardPreview({ card_type: event.card_type, card_data: event.card_data })
          }
          break
        case 'message': {
          const msg = event.message
          if (!current) {
            // First message of a fresh chat: adopt the new conversation.
            // Only the user's own echo may adopt a blank view — background
            // assistant traffic (automations, messengers) must not hijack it.
            if (msg.role === 'user') {
              setActiveId(msg.conversation_id)
              setMessages([msg])
            }
          } else if (msg.conversation_id === current) {
            setMessages((prev) => {
              const existing = prev.findIndex((item) => item.id === msg.id)
              if (existing === -1) return [...prev, msg]
              const next = [...prev]
              next[existing] = msg
              return next
            })
          }
          if (msg.role === 'assistant' && isCurrentConversationEvent(msg.conversation_id, current)) {
            if (rafRef.current) {
              cancelAnimationFrame(rafRef.current)
              rafRef.current = 0
            }
            streamRef.current = ''
            setStreamText('')
            setCardPreview(null)
            setPortraitThinking({
              state: 'done',
              phrase: 'Done — that was a good one.',
              pet_animation: 'happy'
            })
            if (!document.hasFocus()) {
              void window.collie?.petCommand(
                'status:happy|Finished. Your result is ready in Collie.'
              )
            }
            void refreshRuntimeStatus()
            void refreshCommandCatalog()
          }
          void refreshConversations()
          break
        }
        case 'conversation_updated':
          void refreshConversations()
          break
        case 'task_state':
          // Progress belongs to one conversation. Background routines and other chats stay hidden.
          if (event.conversation_id === current) {
            recordTaskStateEvent(taskSnapshotGenerationRef.current, event.conversation_id)
            applyTaskSnapshot(event.conversation_id, event.task)
          }
          break
        case 'error': {
          const key = event.conversation_id || current
          if (key) {
            setTaskTimings((previous) => {
              const existing = previous[key]
              if (!existing || existing.completedMs !== undefined) return previous
              return {
                ...previous,
                [key]: {
                  ...existing,
                  completedMs: Math.max(0, Date.now() - existing.startedAt)
                }
              }
            })
            setThinkingMap((prev) => {
              const next = { ...prev }
              delete next[key]
              return next
            })
          }
          if (event.message && (!event.conversation_id || event.conversation_id === current)) {
            if (rafRef.current) {
              cancelAnimationFrame(rafRef.current)
              rafRef.current = 0
            }
            streamRef.current = ''
            setErrorText(event.message)
            setStreamText('')
            setCardPreview(null)
            setPortraitThinking({
              state: 'error',
              phrase: 'I hit a snag. I am still here.',
              pet_animation: 'concerned'
            })
            if (!document.hasFocus()) {
              void window.collie?.petCommand(
                'status:concerned|I hit a snag. Open Collie for details.'
              )
            }
          }
          break
        }
        case 'run_started':
          setActiveRoutineRuns((current) => new Set(current).add(event.run.id))
          break
        case 'run_completed':
        case 'run_failed':
          setActiveRoutineRuns((current) => {
            const next = new Set(current)
            next.delete(event.run.id)
            return next
          })
          break
        default:
          break
      }
    })
    return off
  }, [applyTaskSnapshot, hydrateActiveTask, onNavigate, refreshCommandCatalog, refreshConversations, refreshRuntimeStatus])

  const activeThinking: ThinkingState | null = activeId
    ? (thinkingMap[activeId] ?? null)
    : null
  const activeAgents = (runtimeStatus.active_agents || []).filter(
    (agent) => agent.conversation_id === activeId
  )
  const activeTiming = activeId ? taskTimings[activeId] : undefined
  const activeTask = activeId ? tasksByConversation[activeId] : undefined
  const taskIsActive = activeTask ? !isTaskTerminal(activeTask) : false
  const isWorkActive = activeThinking !== null || activeAgents.length > 0 || taskIsActive
  const activeAgentsCount = runtimeStatus.active_agents?.length ?? 0
  const thinkingCount = Object.keys(thinkingMap).length

  useEffect(() => {
    if (!activityReady) return
    void window.collie.updateActiveWork({
      chats: thinkingCount,
      approvals: approvals.length,
      routines: activeRoutineRuns.size,
      externalActions: activeAgentsCount
    })
    // Depend on primitives only: a fresh active_agents array from every
    // status poll must not re-fire this IPC call.
  }, [
    activeAgentsCount,
    activeRoutineRuns,
    activityReady,
    approvals.length,
    thinkingCount
  ])

  const elapsedLabel = activeTiming ? (
    <ElapsedLabel timing={activeTiming} />
  ) : undefined

  const send = useCallback(
    async (text: string, attachments: AttachmentDraft[]) => {
      setErrorText('')
      const conversationId = activeIdRef.current
      if (isWorkActive && conversationId) {
        try {
          await collieClient.steerConversation(conversationId, text)
        } catch (error) {
          setErrorText(
            error instanceof Error
              ? error.message
              : 'I could not add those instructions to the active task.'
          )
        }
        return
      }
      if (conversationId) {
        setTaskTimings((previous) => ({
          ...previous,
          [conversationId]: { startedAt: Date.now() }
        }))
      }
      setPortraitThinking({
        state: 'startup',
        phrase: 'Thinking through your task…',
        pet_animation: 'working'
      })
      try {
        const { conversation_id, command_handled } = await collieClient.chat(
          activeIdRef.current,
          text,
          attachments,
          executionMode,
          currentProject || undefined
        )
        if (activeIdRef.current !== conversation_id) {
          if (command_handled) {
            await openConversation(conversation_id)
          } else {
            setActiveId(conversation_id)
          }
        }
        if (command_handled) void refreshCommandCatalog()
        void refreshConversations()
      } catch (e) {
        setErrorText(e instanceof Error ? e.message : String(e))
        setPortraitThinking({
          state: 'error',
          phrase: 'I could not start that response.',
          pet_animation: 'concerned'
        })
      }
    },
    [
      currentProject,
      executionMode,
      isWorkActive,
      openConversation,
      refreshCommandCatalog,
      refreshConversations
    ]
  )

  const changeExecutionMode = useCallback((mode: ExecutionMode) => {
    setExecutionMode(mode)
    localStorage.setItem('collie.executionMode', mode)
    const conversationId = activeIdRef.current
    if (conversationId) {
      void collieClient.setExecutionMode(conversationId, mode).catch(() => undefined)
    }
  }, [])

  useEffect(() => {
    const requestChange = (event: Event): void => {
      const detail = (event as CustomEvent<PlanChangeRequest>).detail
      const conversationId = activeIdRef.current
      if (!detail || !conversationId) return

      const report = (state: 'requesting' | 'pending_safe_boundary' | 'paused' | 'error', message: string): void => {
        publishPlanChangeResult({ ...detail, state, message })
        if (activeIdRef.current === conversationId) setPlanChangeNotice(message)
      }

      const activePlanRun = activeTask?.source === 'plan_run' ? activeTask : null
      if (!activePlanRun) {
        // This is still a proposed plan: no run exists to pause.
        changeExecutionMode('plan')
        report('paused', 'Tell me what you would like to change, and I will revise the plan.')
        return
      }

      report('requesting', 'Asking Collie to pause at a safe boundary…')
      void collieClient
        .changePlan(conversationId, activePlanRun.id)
        .then((result) => {
          if (!result.requested || result.conversation_id !== conversationId || result.run_id !== activePlanRun.id) {
            throw new Error('Collie could not confirm a safe plan change.')
          }
          // The authenticated backend acknowledgement is the point at which Plan mode is safe.
          if (activeIdRef.current === conversationId) {
            setExecutionMode('plan')
            localStorage.setItem('collie.executionMode', 'plan')
          }
          if (result.status === 'pending_safe_boundary') {
            report(
              'pending_safe_boundary',
              'Collie is finishing its current safe step, then the plan will pause. Tell me what you would like changed.'
            )
          } else {
            report('paused', 'The current plan stopped at a safe boundary. Tell me what you would like to change.')
          }
        })
        .catch((error: unknown) => {
          report(
            'error',
            error instanceof Error ? error.message : 'Collie could not pause the plan safely. Please try again.'
          )
        })
    }
    window.addEventListener(PLAN_CHANGE_REQUEST_EVENT, requestChange)
    return () => window.removeEventListener(PLAN_CHANGE_REQUEST_EVENT, requestChange)
  }, [activeTask, changeExecutionMode])

  const newChat = useCallback(() => {
    onNavigate('chat')
    void openConversation(null)
  }, [onNavigate, openConversation])

  const addProject = useCallback(async () => {
    try {
      const path = await window.collie.pickProjectFolder()
      if (!path) return
      rememberProject(path)
      onNavigate('chat')
      await openConversation(null)
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'I could not open that folder.')
    }
  }, [onNavigate, openConversation, rememberProject])

  const openProject = useCallback((path: string) => {
    rememberProject(path)
    onNavigate('chat')
    void openConversation(null)
  }, [onNavigate, openConversation, rememberProject])

  const deleteConversation = useCallback(
    async (id: string) => {
      const title =
        conversations.find((item) => item.id === id)?.title || 'this conversation'
      if (!window.confirm(`Delete "${title}"? This removes it and its files.`)) return
      try {
        await collieClient.deleteConversation(id)
      } catch {
        setErrorText('I could not delete that conversation. Try again?')
        return
      }
      if (activeIdRef.current === id) {
        void openConversation(null)
      }
      void refreshConversations()
    },
    [conversations, openConversation, refreshConversations]
  )

  const stop = useCallback(async () => {
    const conversationId = activeIdRef.current
    if (!conversationId) return
    try {
      const result = await collieClient.stopConversation(conversationId)
      setRuntimeStatus((previous) => ({
        ...previous,
        active_agents: (previous.active_agents || []).filter(
          (agent) => agent.conversation_id !== conversationId
        )
      }))
      setThinkingMap((previous) => {
        const next = { ...previous }
        delete next[conversationId]
        return next
      })
      setTaskTimings((previous) => {
        const existing = previous[conversationId]
        if (!existing || existing.completedMs !== undefined) return previous
        return {
          ...previous,
          [conversationId]: {
            ...existing,
            completedMs: Math.max(0, Date.now() - existing.startedAt)
          }
        }
      })
      setPortraitThinking({
        state: 'idle',
        phrase: result.cancelled_subagents
          ? `Stopped everything, including ${result.cancelled_subagents} agent${result.cancelled_subagents === 1 ? '' : 's'}.`
          : 'Stopped. Ready when you are.',
        pet_animation: 'idle'
      })
    } catch {
      setErrorText('I could not stop that response cleanly.')
    }
  }, [])

  useEffect(() => {
    if (!isWorkActive) return
    const timer = setInterval(() => void refreshRuntimeStatus(), 1200)
    return () => clearInterval(timer)
  }, [isWorkActive, refreshRuntimeStatus])

  const changeProvider = useCallback(async (providerId: string) => {
    setErrorText('')
    setPortraitThinking({
      state: 'searching',
      phrase: 'Switching models…',
      pet_animation: 'walk'
    })
    try {
      const result = await collieClient.activateProvider(providerId)
      if (!result.configured) throw new Error(result.error || 'That model could not connect.')
      await refreshRuntimeStatus()
      setPortraitThinking({ state: 'done', phrase: 'Model switched.', pet_animation: 'happy' })
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error))
      setPortraitThinking({
        state: 'error',
        phrase: 'The previous model is still selected.',
        pet_animation: 'concerned'
      })
    }
  }, [refreshRuntimeStatus])

  return (
    <div className="app-shell flex h-full">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        busyIds={new Set(Object.keys(thinkingMap))}
        activeView={activeView}
        onNavigate={onNavigate}
        onSelect={(id) => {
          onNavigate('chat')
          const conversation = conversations.find((item) => item.id === id)
          void openConversation(
            id,
            conversation?.project_path ?? null
          )
        }}
        onNewChat={newChat}
        onDelete={(id) => void deleteConversation(id)}
        workspace={currentProject}
        projects={projects}
        onProjectChange={openProject}
        onAddProject={() => void addProject()}
      />
      {activeView === 'settings' ? (
        <main className="settings-workspace min-w-0 flex-1 overflow-hidden">
          <SettingsScreen onRedoOnboarding={onRedoOnboarding} onNavigate={onNavigate} />
        </main>
      ) : activeView === 'agents' ? (
        <AgentsScreen />
      ) : activeView === 'skills' ? (
        <SkillsScreen />
      ) : activeView === 'loops' ? (
        <RoutinesScreen />
      ) : activeView === 'connectors' ? (
        <ConnectorsScreen />
      ) : (
      <main className="workspace flex min-w-0 flex-1 flex-col">
        <header className="workspace-header">
          <div>
            <h1>
              {activeId
                ? conversations.find((item) => item.id === activeId)?.title || 'Conversation'
                : 'New conversation'}
            </h1>
          </div>
        </header>
        <div className="conversation-panel">
          <MessageList messages={messages} streamText={streamText} cardPreview={cardPreview} />
        {errorText && (
          <div className="mx-4 mb-2 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
            style={{ borderColor: 'var(--collie-snoot)' }}>
            <CollieFace size={16} />
            <span>{errorText}</span>
          </div>
        )}
        {planChangeNotice ? (
          <div
            className="mx-4 mb-2 rounded-lg border px-3 py-2 text-sm"
            style={{ borderColor: 'var(--collie-border)', color: 'var(--collie-text-muted)' }}
            role="status"
            aria-live="polite"
          >
            {planChangeNotice}
          </div>
        ) : null}
          {approvals.map((approval) => (
            <ApprovalSheet
              key={approval.id}
              approval={approval}
              inline
              onResolved={() =>
                setApprovals((current) => current.filter((item) => item.id !== approval.id))
              }
            />
          ))}
          {activeTask && !isTaskTerminal(activeTask) ? <TaskProgress task={activeTask} onStop={() => void stop()} /> : null}
          <div className="portrait-composer-layout">
            <InteractiveColliePortrait
              thinking={portraitThinking}
              phrase={portraitThinking?.phrase || 'Ready when you are.'}
              elapsedLabel={elapsedLabel}
              isTyping={isTyping}
              activeAgents={activeAgents}
            />
            <ChatInput
              onSend={(text, attachments) => void send(text, attachments)}
              onStop={() => void stop()}
              busy={isWorkActive}
              steering={isWorkActive}
              mode={executionMode}
              onModeChange={changeExecutionMode}
              model={runtimeStatus.model}
              workspace={currentProject}
              projects={projects}
              providers={runtimeStatus.providers}
              onProviderChange={(providerId) => void changeProvider(providerId)}
              onProjectChange={rememberProject}
              onAddProject={() => void addProject()}
              onAddModel={() => onNavigate('settings')}
              onTypingChange={setIsTyping}
              onTranscribe={async (audio) => (await collieClient.transcribe(audio)).text}
              commandCatalog={commandCatalog}
            />
          </div>
        </div>
      </main>
      )}
      {activeView !== 'chat' && approvals.length > 0 ? (
        <div className="fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
          {approvals.map((approval, index) => (
            <ApprovalSheet
              key={approval.id}
              approval={approval}
              activeModal={index === approvals.length - 1}
              onResolved={() =>
                setApprovals((current) => current.filter((item) => item.id !== approval.id))
              }
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}
