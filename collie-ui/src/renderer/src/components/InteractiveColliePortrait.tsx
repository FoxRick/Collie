import { useEffect, useMemo, useRef, useState } from 'react'
import { Ban, Check, CircleX } from 'lucide-react'
import type { ActiveAgent, ThinkingState } from '../lib/ipc'
import {
  agentActivityLine,
  agentElapsedMs,
  agentOutcomeLabel,
  agentPhaseLabel,
  formatAgentElapsed,
  isAgentSettled,
  settledRowsWithinWindow
} from '../lib/agentActivity'
import {
  PORTRAIT_STATIC_FALLBACK,
  STATUS_COPY,
  type PortraitState
} from './portraitStates'
import { useColliePortraitState } from './useColliePortraitState'
import AgentAvatar from './AgentAvatar'
import ColliePortraitFrame from './ColliePortraitFrame'

const MAX_WORKING_ROWS = 3
const MAX_SETTLED_ROWS = 2

interface Props {
  thinking: ThinkingState | null
  phrase: string
  elapsedLabel?: React.ReactNode
  isTyping: boolean
  activeAgents: ActiveAgent[]
  /** Settled rows for this conversation (recent_agents), newest first. */
  recentAgents?: ActiveAgent[]
}

function OutcomeIcon({ outcome }: { outcome: ActiveAgent['outcome'] }): React.JSX.Element {
  if (outcome === 'error') return <CircleX size={12} className="agent-outcome-icon is-error" aria-hidden />
  if (outcome === 'cancelled') return <Ban size={12} className="agent-outcome-icon is-cancelled" aria-hidden />
  return <Check size={12} className="agent-outcome-icon is-ok" aria-hidden />
}

/** One compact row: dog portrait, name + elapsed, one-line activity/status. */
function AgentPopupRow({
  agent,
  now,
  settled
}: {
  agent: ActiveAgent
  now: number
  settled: boolean
}): React.JSX.Element {
  const elapsed = agentElapsedMs(agent, now)
  const elapsedLabel = elapsed !== null ? formatAgentElapsed(elapsed) : ''
  return (
    <span
      className={settled ? 'portrait-agent-row is-settled' : 'portrait-agent-row'}
      title={`${agent.name} · ${agentPhaseLabel(agent)}`}
    >
      <AgentAvatar name={agent.name} size={34} />
      <b>
        {agent.name}
        <em className="portrait-agent-elapsed">{elapsedLabel}</em>
      </b>
      <small>
        {settled ? (
          <>
            <OutcomeIcon outcome={agent.outcome} />
            {agentOutcomeLabel(agent.outcome)}
          </>
        ) : (
          <>
            <span className="agent-live-dot" aria-hidden />
            {agentActivityLine(agent)}
          </>
        )}
      </small>
    </span>
  )
}

export default function InteractiveColliePortrait({
  thinking,
  phrase,
  elapsedLabel,
  isTyping,
  activeAgents,
  recentAgents = []
}: Props): React.JSX.Element {
  const ringRef = useRef<HTMLDivElement>(null)
  const [copyIndex, setCopyIndex] = useState(0)
  const [now, setNow] = useState(Date.now)
  const [preview, setPreview] = useState<PortraitState | ''>('')
  const previewEnabled = sessionStorage.getItem('collie.ui-ux-preview') === '1' &&
    sessionStorage.getItem('collie.expression-preview') === '1'
  const { state: liveState, paused, reducedMotion, triggerReaction, triggerHappyReaction } = useColliePortraitState(
    thinking,
    isTyping,
    activeAgents
  )
  const state = previewEnabled && preview ? preview : liveState
  const settledAfterTerminal =
    ['idle', 'sleepy'].includes(state) &&
    ['done', 'error'].includes(thinking?.state || '')
  const effectivePhrase = settledAfterTerminal ? 'Ready when you are.' : phrase
  const copyPool = useMemo(() => {
    const pool = STATUS_COPY[state] || []
    return effectivePhrase
      ? [
          effectivePhrase.replace(/^\*|\*$/g, ''),
          ...pool.filter((item) => item !== effectivePhrase)
        ]
      : pool
  }, [effectivePhrase, state])

  useEffect(() => {
    setCopyIndex(0)
    if (copyPool.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setInterval(
      () => setCopyIndex((current) => (current + 1) % copyPool.length),
      2800
    )
    return () => window.clearInterval(timer)
  }, [copyPool])

  // One 1 s tick so agent rows' elapsed labels stay live without re-rendering
  // the whole chat screen; freezes as soon as nothing visible remains. The
  // show flags derive from the VISIBLE (expiry-filtered) rows so that once
  // the last settled row ages out, the ticker is cleared and the empty
  // container unmounts instead of lingering forever.
  const workingRows = activeAgents.slice(0, MAX_WORKING_ROWS)
  const overflowCount = activeAgents.length - workingRows.length
  const settledRows = settledRowsWithinWindow(recentAgents, now)
    .filter((agent) => isAgentSettled(agent))
    .slice(0, MAX_SETTLED_ROWS)
  const showWorking = workingRows.length > 0
  const showSettled = settledRows.length > 0
  useEffect(() => {
    if (!showWorking && !showSettled) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [showWorking, showSettled])

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    if (event.shiftKey) triggerHappyReaction()
    else triggerReaction()
  }

  const status = copyPool[copyIndex] || 'Ready when you are.'

  return (
    <section
      className={`collie-portrait-stage is-${state}${paused ? ' is-paused' : ''}`}
      aria-label={`Collie is ${state.replaceAll('_', ' ')}`}
    >
      <div
        ref={ringRef}
        className="collie-portrait-ring"
        role="button"
        tabIndex={0}
        aria-label="Pet Collie"
        aria-keyshortcuts="Enter Space Shift+Enter"
        onClick={triggerReaction}
        onDoubleClick={triggerHappyReaction}
        onKeyDown={handleKeyDown}
      >
        <span className="collie-ring-background" aria-hidden="true" />
        <div className="collie-portrait-clip">
          <ColliePortraitFrame
            className="collie-portrait-base"
            state={state}
            reducedMotion={reducedMotion}
            fallbackSrc={PORTRAIT_STATIC_FALLBACK}
          />
        </div>
        <span className="collie-ring-foreground" aria-hidden="true" />
      </div>
      <div className="collie-portrait-status" role="status" aria-live="polite">
        <span key={status}>{status}</span>
        {elapsedLabel ? <small>{elapsedLabel}</small> : null}
      </div>
      {previewEnabled && <label className="portrait-expression-preview" style={{ fontSize: 11 }}>
        Expression preview
        <select aria-label="Preview expression" value={preview} onChange={event => setPreview(event.target.value as PortraitState | '')}>
          <option value="">Live app state</option>
          {(['idle', 'attentive', 'thinking', 'working', 'review', 'delegating', 'listening', 'writing', 'waiting', 'completion', 'error', 'sleepy'] as PortraitState[]).map(item =>
            <option key={item} value={item}>{item}</option>)}
        </select>
      </label>}
      {(showWorking || showSettled) && (
        <div
          className="portrait-agent-list"
          aria-label={`${activeAgents.length} agents working, ${settledRows.length} recently finished`}
        >
          {workingRows.map((agent) => (
            <AgentPopupRow key={`w-${agent.id}`} agent={agent} now={now} settled={false} />
          ))}
          {overflowCount > 0 && (
            <span className="portrait-agent-overflow" aria-hidden>
              +{overflowCount} more
            </span>
          )}
          {settledRows.map((agent) => (
            <AgentPopupRow key={`s-${agent.id}`} agent={agent} now={now} settled />
          ))}
        </div>
      )}
    </section>
  )
}
