import { useEffect, useMemo, useState } from 'react'
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
import { quantizePortraitPointer } from './colliePortraitMotion'
import {
  PORTRAIT_STATIC_FALLBACK,
  STATUS_COPY
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
      <AgentAvatar identity={agent.id || agent.name} name={agent.name} size={34} />
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
  const [pointerTarget, setPointerTarget] = useState<number | null>(null)
  const [copyIndex, setCopyIndex] = useState(0)
  const [now, setNow] = useState(Date.now)
  const { state, paused, reducedMotion, triggerReaction } = useColliePortraitState(
    thinking,
    isTyping,
    activeAgents,
    pointerTarget
  )
  const settledAfterTerminal =
    ['idle', 'sleepy'].includes(state) &&
    ['done', 'error'].includes(thinking?.state || '')
  const effectivePhrase =
    state === 'pointer_look'
      ? 'I’m listening…'
      : settledAfterTerminal
        ? 'Ready when you are.'
        : phrase
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

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>): void {
    const bounds = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - bounds.left - bounds.width / 2) / (bounds.width / 2)
    const y = (event.clientY - bounds.top - bounds.height / 2) / (bounds.height / 2)
    setPointerTarget(quantizePortraitPointer(x, y))
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    triggerReaction()
  }

  const status = copyPool[copyIndex] || 'Ready when you are.'

  return (
    <section
      className={`collie-portrait-stage is-${state}${paused ? ' is-paused' : ''}`}
      aria-label={`Collie is ${state.replaceAll('_', ' ')}`}
    >
      <div
        className="collie-portrait-ring"
        role="button"
        tabIndex={0}
        aria-label="Pet Collie"
        data-gaze-direction={pointerTarget ?? 'idle'}
        onPointerMove={handlePointerMove}
        onPointerLeave={() => setPointerTarget(null)}
        onClick={triggerReaction}
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
