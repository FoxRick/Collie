import { useCallback, useEffect, useRef, useState } from 'react'
import {
  collieClient,
  type AttachmentDraft,
  type ApprovalPreset,
  type ApprovalRequest,
  type CollieEvent,
  type CollieMessage,
  type CommandCatalog,
  type Conversation,
  type ExecutionMode,
  type FileAccessScope,
  type RuntimeStatus,
  type TaskState,
  type Thing,
  type ThinkingState
} from '../lib/ipc'
import Sidebar from '../components/Sidebar'
import MessageList from '../components/MessageList'
import RememberPill from '../components/RememberPill'
import ChatInput from '../components/ChatInput'
import CollieFace from '../components/CollieFace'
import InteractiveColliePortrait from '../components/InteractiveColliePortrait'
import ThingPanel from '../components/things/ThingPanel'
import ThingsToggle from '../components/things/ThingsToggle'
import SettingsScreen from './SettingsScreen'
import AgentsScreen from './AgentsScreen'
import SkillsScreen from './SkillsScreen'
import RoutinesScreen from './RoutinesScreen'
import ConnectorsScreen from './ConnectorsScreen'
import type { AppView } from '../lib/navigation'
import { mergeStreamDelta, nextStreamReveal, shouldResetStreamDisplay, visibleStreamText } from '../lib/stream'
import ApprovalSheet from '../components/approvals/ApprovalSheet'
import { isTaskTerminal } from '../components/tasks/TaskProgress'
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
  /** True right after first connect: open the starter conversation, no empty state. */
  autoOpenStarter?: boolean
}

interface TaskTiming {
  startedAt: number
  completedMs?: number
}

const FILE_ACCESS_STORAGE_KEY = 'collie.fileAccessScope'

export function loadFileAccessScope(): FileAccessScope {
  try {
    const parsed = JSON.parse(localStorage.getItem(FILE_ACCESS_STORAGE_KEY) || '{}') as {
      mode?: unknown
      roots?: unknown
    }
    if (parsed.mode === 'chosen_folders' && Array.isArray(parsed.roots)) {
      const roots = parsed.roots.filter(
        (root): root is string => typeof root === 'string' && Boolean(root.trim())
      )
      if (roots.length) return { mode: 'chosen_folders', roots }
    }
  } catch {
    // A damaged preference falls back to the narrow selected-folder scope.
  }
  return { mode: 'selected_folder' }
}

export function persistFileAccessScope(
  scope: FileAccessScope,
  storage: Pick<Storage, 'setItem'> = localStorage
): void {
  if (scope.mode === 'full_file_access') return
  storage.setItem(FILE_ACCESS_STORAGE_KEY, JSON.stringify(scope))
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
  onRedoOnboarding,
  autoOpenStarter = false
}: Props): React.JSX.Element {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<CollieMessage[]>([])
  const [streamText, setStreamText] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [thinkingMap, setThinkingMap] = useState<Record<string, ThinkingState>>({})
  const [errorText, setErrorText] = useState('')
  const [planChangeNotice, setPlanChangeNotice] = useState('')
  const [portraitThinking, setPortraitThinking] = useState<ThinkingState | null>(null)
  const [isTyping, setIsTyping] = useState(false)
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(() =>
    localStorage.getItem('collie.executionMode') === 'plan' ? 'plan' : 'execute'
  )
  const [approvalPreset, setApprovalPreset] = useState<ApprovalPreset>('ask')
  const [fileAccessScope, setFileAccessScope] = useState<FileAccessScope>(loadFileAccessScope)
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
  const [things, setThings] = useState<Thing[]>([])
  const [thingsOpen, setThingsOpen] = useState(false)
  const [thingsUnseen, setThingsUnseen] = useState<Set<string>>(new Set())
  const thingsRef = useRef<Thing[]>([])
  const thingsOpenRef = useRef(false)
  /** A manual close of the panel is respected for the rest of this chat. */
  const thingsManuallyClosedRef = useRef(false)
  const activeIdRef = useRef<string | null>(null)
  const streamRef = useRef('')
  const streamDisplayRef = useRef('')
  const streamTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingAssistantRef = useRef<CollieMessage | null>(null)
  const finalizePendingRef = useRef<(() => void) | null>(null)
  const loadTokenRef = useRef(0)
  const taskSnapshotGenerationRef = useRef<Record<string, number>>({})
  activeIdRef.current = activeId

  const stopStreamReveal = useCallback(() => {
    if (streamTimerRef.current) {
      clearTimeout(streamTimerRef.current)
      streamTimerRef.current = null
    }
  }, [])

  const scheduleStreamReveal = useCallback(() => {
    if (streamTimerRef.current) return

    const reveal = () => {
      const next = nextStreamReveal(streamDisplayRef.current, streamRef.current)
      streamDisplayRef.current = next
      const pending = pendingAssistantRef.current
      if (pending && activeIdRef.current === pending.conversation_id) {
        // Once the terminal event arrives, its place in the transcript is
        // fixed. Continue revealing into that reserved bubble so a later user
        // event cannot jump ahead of the paced assistant response.
        setMessages((previous) =>
          previous.map((item) => item.id === pending.id ? { ...item, content: next } : item)
        )
        setStreamText('')
      } else {
        setStreamText(next)
      }

      if (next !== visibleStreamText(streamRef.current)) {
        streamTimerRef.current = setTimeout(reveal, 32)
      } else {
        streamTimerRef.current = null
        finalizePendingRef.current?.()
      }
    }

    streamTimerRef.current = setTimeout(reveal, 32)
  }, [])

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

  const refreshApprovalPreset = useCallback(async () => {
    try {
      const { settings } = await collieClient.getSettings()
      const stored = settings['permissions.local_write_preset']
      if (stored === 'ask' || stored === 'allow') setApprovalPreset(stored)
    } catch {
      // Keep the conservative Ask me fallback until the core is available.
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

  const finalizePendingAssistant = useCallback(() => {
    const msg = pendingAssistantRef.current
    if (!msg || activeIdRef.current !== msg.conversation_id) return
    pendingAssistantRef.current = null
    setMessages((previous) => {
      const existing = previous.findIndex((item) => item.id === msg.id)
      if (existing === -1) return [...previous, msg]
      const next = [...previous]
      next[existing] = msg
      return next
    })
    streamRef.current = ''
    streamDisplayRef.current = ''
    setStreamText('')
    setCardPreview(null)
    setPortraitThinking({
      state: 'done',
      phrase: 'Done — that was a good one.',
      pet_animation: 'happy'
    })
    if (!document.hasFocus()) {
      void window.collie?.petCommand('status:happy|Finished. Your result is ready in Collie.')
    }
    void refreshRuntimeStatus()
    void refreshCommandCatalog()
  }, [refreshCommandCatalog, refreshRuntimeStatus])
  finalizePendingRef.current = finalizePendingAssistant

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
    setStreaming(false)
    setErrorText('')
    setPlanChangeNotice('')
    setPortraitThinking(null)
    setCardPreview(null)
    setThings([])
    thingsRef.current = []
    setThingsOpen(false)
    thingsOpenRef.current = false
    thingsManuallyClosedRef.current = false
    setThingsUnseen(new Set())
    streamRef.current = ''
    streamDisplayRef.current = ''
    pendingAssistantRef.current = null
    stopStreamReveal()
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
        void collieClient
          .listThings(id)
          .then(({ things: loaded }) => {
            if (token !== loadTokenRef.current) return
            thingsRef.current = loaded
            setThings(loaded)
          })
          .catch(() => undefined)
      }
    } catch {
      if (token === loadTokenRef.current) setMessages([])
    }
  }, [hydrateActiveTask, rememberProject, stopStreamReveal])

  const toggleThingsPanel = useCallback((): void => {
    const next = !thingsOpenRef.current
    thingsOpenRef.current = next
    setThingsOpen(next)
    if (next) {
      // Opening the panel counts as viewing everything currently inside it.
      setThingsUnseen(new Set())
    } else {
      thingsManuallyClosedRef.current = true
    }
  }, [])

  const closeThingsPanel = useCallback((): void => {
    thingsOpenRef.current = false
    setThingsOpen(false)
    thingsManuallyClosedRef.current = true
  }, [])

  const handleThingOpen = useCallback((thing: Thing): void => {
    void window.collie.thingOpen(thing.path)
  }, [])

  const handleThingShowInFolder = useCallback((thing: Thing): void => {
    void window.collie.thingShowInFolder(thing.path)
  }, [])

  const handleThingSaveCopy = useCallback((thing: Thing): void => {
    void window.collie.thingSaveCopy(thing.path, thing.title).catch(() => undefined)
  }, [])

  useEffect(() => () => {
    stopStreamReveal()
    pendingAssistantRef.current = null
    finalizePendingRef.current = null
  }, [stopStreamReveal])

  useEffect(() => {
    void Promise.all([
      refreshConversations(),
      refreshRuntimeStatus(),
      refreshCommandCatalog(),
      refreshApprovalPreset(),
      collieClient
        .listPendingApprovals()
        .then((data) => setApprovals(data.approvals))
        .catch(() => undefined)
    ]).finally(() => setActivityReady(true))
  }, [refreshApprovalPreset, refreshCommandCatalog, refreshConversations, refreshRuntimeStatus])

  useEffect(() => {
    if (activeView === 'chat') void refreshApprovalPreset()
  }, [activeView, refreshApprovalPreset])

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
          void refreshApprovalPreset()
          setThinkingMap({})
          setStreaming(false)
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
          if (key === current) setStreaming(!terminal)
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
            setStreaming(true)
            scheduleStreamReveal()
          }
          break
        case 'card':
          if (event.conversation_id === current) {
            setCardPreview({ card_type: event.card_type, card_data: event.card_data })
          }
          break
        case 'artifact':
          if (event.conversation_id === current) {
            const thing = event.artifact
            const firstThing = thingsRef.current.length === 0
            const existing = thingsRef.current.find((t) => t.id === thing.id)
            thingsRef.current = [
              thing,
              ...thingsRef.current.filter((t) => t.id !== thing.id)
            ]
            setThings(thingsRef.current)
            // A brand-new thing is unseen until the panel (or its card) is
            // opened; a revision of an existing thing keeps its seen state.
            if (!existing && !thingsOpenRef.current) {
              setThingsUnseen((prev) => new Set(prev).add(thing.id))
            }
            // Calm default: the first thing of a chat opens the panel once;
            // a manual close is respected for the rest of that chat.
            if (firstThing && !thingsManuallyClosedRef.current) {
              thingsOpenRef.current = true
              setThingsOpen(true)
              setThingsUnseen(new Set())
            }
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
          } else if (msg.conversation_id === current && msg.role !== 'assistant') {
            setMessages((prev) => {
              const existing = prev.findIndex((item) => item.id === msg.id)
              if (existing === -1) return [...prev, msg]
              const next = [...prev]
              next[existing] = msg
              return next
            })
          }
          if (msg.role === 'assistant' && isCurrentConversationEvent(msg.conversation_id, current)) {
            pendingAssistantRef.current = msg
            // The turn's final text is reserved and revealed in the
            // transcript — the live stream card hands over to it.
            setStreaming(false)
            // A mid-turn steer delivers the superseded answer as its own
            // message; the follow-up response then covers only the tail of
            // the accumulated stream. Reveal that bubble from scratch instead
            // of rewinding the already-delivered text.
            if (shouldResetStreamDisplay(streamRef.current, msg.content)) {
              streamDisplayRef.current = ''
            }
            // Reserve the assistant turn as soon as it is complete. The
            // reveal timer replaces this provisional content in place, rather
            // than appending after newer user turns.
            const displayedContent = streamDisplayRef.current
            setMessages((previous) => {
              const existing = previous.findIndex((item) => item.id === msg.id)
              const reserved = { ...msg, content: displayedContent }
              if (existing === -1) return [...previous, reserved]
              const next = [...previous]
              next[existing] = reserved
              return next
            })
            setStreamText('')
            if (streamRef.current || streamDisplayRef.current) {
              streamRef.current = msg.content
              scheduleStreamReveal()
            } else {
              finalizePendingAssistant()
            }
            void refreshConversations()
            break
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
            stopStreamReveal()
            streamRef.current = ''
            streamDisplayRef.current = ''
            setStreaming(false)
            const pending = pendingAssistantRef.current
            pendingAssistantRef.current = null
            if (pending) {
              setMessages((previous) => previous.filter((item) => item.id !== pending.id))
            }
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
  }, [applyTaskSnapshot, finalizePendingAssistant, hydrateActiveTask, onNavigate, refreshApprovalPreset, refreshCommandCatalog, refreshConversations, refreshRuntimeStatus, scheduleStreamReveal, stopStreamReveal])

  const activeThinking: ThinkingState | null = activeId
    ? (thinkingMap[activeId] ?? null)
    : null
  const activeAgents = (runtimeStatus.active_agents || []).filter(
    (agent) => agent.conversation_id === activeId
  )
  const recentAgents = (runtimeStatus.recent_agents || []).filter(
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
      setStreaming(true)
      try {
        const { conversation_id, command_handled } = await collieClient.chat(
          activeIdRef.current,
          text,
          attachments,
          executionMode,
          currentProject || undefined,
          fileAccessScope
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
      fileAccessScope,
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

  const changeApprovalPreset = useCallback((preset: ApprovalPreset) => {
    const previous = approvalPreset
    setApprovalPreset(preset)
    void collieClient.setApprovalPreset(preset).catch((error: unknown) => {
      setApprovalPreset(previous)
      setErrorText(error instanceof Error ? error.message : 'I could not change approval mode.')
    })
  }, [approvalPreset])

  const changeFileAccessScope = useCallback((scope: FileAccessScope) => {
    setFileAccessScope(scope)
    persistFileAccessScope(scope)
    // Apply the choice to the in-flight turn right away (the core keeps a
    // live per-conversation override that local file tools consult), not
    // only to the next message.
    const conversationId = activeIdRef.current
    if (conversationId) {
      collieClient.setFileAccessScope(conversationId, scope).catch(() => undefined)
    }
  }, [])

  const changeComposerProject = useCallback((path: string) => {
    if (!path) {
      // A conversation can persist its old project path in the core. Starting
      // a fresh General Chat makes the composer label and effective file scope
      // agree instead of silently retaining that previous project.
      void openConversation(null, '')
      return
    }
    rememberProject(path)
  }, [openConversation, rememberProject])

  const chooseFileAccessFolders = useCallback(async () => {
    try {
      const roots = await window.collie.pickFileAccessFolders()
      if (!roots.length) return
      changeFileAccessScope({ mode: 'chosen_folders', roots })
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'I could not use those folders.')
    }
  }, [changeFileAccessScope])

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

  /** Open (or create, once) the getting-started conversation with its greeting. */
  const getStarted = useCallback(async () => {
    onNavigate('chat')
    try {
      const { conversation } = await collieClient.getStarterConversation(
        activeIdRef.current
      )
      await openConversation(conversation.id)
      void refreshConversations()
    } catch {
      // Core may still be starting — the sidebar lists the conversation once ready.
    }
  }, [onNavigate, openConversation, refreshConversations])

  // After first connect: straight into the starter conversation, never an
  // empty state. Runs once (the prop stays true across remounts of the shell).
  const starterOpenedRef = useRef(false)
  useEffect(() => {
    if (!autoOpenStarter || starterOpenedRef.current) return
    starterOpenedRef.current = true
    void getStarted()
  }, [autoOpenStarter, getStarted])

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
          <SettingsScreen
            onRedoOnboarding={onRedoOnboarding}
            onNavigate={onNavigate}
            onGetStarted={() => void getStarted()}
          />
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
          {things.length > 0 && (
            <ThingsToggle
              unseen={thingsUnseen.size}
              open={thingsOpen}
              onClick={toggleThingsPanel}
            />
          )}
        </header>
        <div className={`workspace-body flex min-h-0 flex-1 ${thingsOpen && things.length > 0 ? 'has-things' : ''}`}>
        <div className="conversation-panel">
          <MessageList messages={messages} streamText={streamText} streaming={streaming} cardPreview={cardPreview} />
          <RememberPill conversationId={activeId} />
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
          {approvals.length > 0 ? (
            <div className="approval-stack">
              {approvals.length > 1 ? (
                <div className="approval-stack-count" role="status">
                  {approvals.length} approvals waiting — approve them one at a time
                </div>
              ) : null}
              <ApprovalSheet
                key={approvals[approvals.length - 1].id}
                approval={approvals[approvals.length - 1]}
                inline
                onResolved={() =>
                  setApprovals((current) =>
                    current.filter(
                      (item) => item.id !== approvals[approvals.length - 1].id
                    )
                  )
                }
              />
            </div>
          ) : null}
          <div className="portrait-composer-layout">
            <InteractiveColliePortrait
              thinking={portraitThinking}
              phrase={portraitThinking?.phrase || 'Ready when you are.'}
              elapsedLabel={elapsedLabel}
              isTyping={isTyping}
              activeAgents={activeAgents}
              recentAgents={recentAgents}
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
              onProjectChange={changeComposerProject}
              onAddProject={() => void addProject()}
              onAddModel={() => onNavigate('settings')}
              approvalPreset={approvalPreset}
              onApprovalPresetChange={changeApprovalPreset}
              fileAccessScope={fileAccessScope}
              onFileAccessScopeChange={changeFileAccessScope}
              onChooseFileAccessFolders={() => void chooseFileAccessFolders()}
              onTypingChange={setIsTyping}
              onTranscribe={async (audio) => (await collieClient.transcribe(audio)).text}
              commandCatalog={commandCatalog}
              taskProgress={activeTask && !isTaskTerminal(activeTask) ? activeTask : null}
              autofocus={autoOpenStarter}
            />
          </div>
        </div>
        {thingsOpen && things.length > 0 && (
          <ThingPanel
            things={things}
            unseenIds={thingsUnseen}
            onClose={closeThingsPanel}
            onOpen={handleThingOpen}
            onSaveCopy={handleThingSaveCopy}
            onShowInFolder={handleThingShowInFolder}
          />
        )}
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
