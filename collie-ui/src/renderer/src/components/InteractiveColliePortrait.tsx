import { useEffect, useMemo, useState } from 'react'
import type { ActiveAgent, ThinkingState } from '../lib/ipc'
import { quantizePortraitPointer } from './colliePortraitMotion'
import {
  PORTRAIT_FRAME_DURATION,
  PORTRAIT_STATIC_FALLBACK,
  portraitFramesFor,
  STATUS_COPY
} from './portraitStates'
import { useColliePortraitState } from './useColliePortraitState'
import AgentAvatar from './AgentAvatar'
import ColliePortraitFrame from './ColliePortraitFrame'

const pawFront = new URL('../assets/portrait/paw-front-brand.webp', import.meta.url).href

interface Props {
  thinking: ThinkingState | null
  phrase: string
  elapsedLabel?: React.ReactNode
  isTyping: boolean
  activeAgents: ActiveAgent[]
}

const PHASE_LABELS: Record<string, string> = {
  initializing: 'Getting ready',
  awaiting_tools: 'Using tools',
  tools_completed: 'Putting it together',
  final_response: 'Wrapping up'
}

function conciseTask(agent: ActiveAgent): string {
  const source = (
    agent.task_description ||
    PHASE_LABELS[agent.phase] ||
    agent.phase ||
    'Working'
  ).replace(/\s+/g, ' ').trim()
  const sentence = source.split(/(?<=[.!?])\s/)[0]
  return sentence.length > 72 ? `${sentence.slice(0, 69).trimEnd()}…` : sentence
}

export default function InteractiveColliePortrait({
  thinking,
  phrase,
  elapsedLabel,
  isTyping,
  activeAgents
}: Props): React.JSX.Element {
  const [pointerTarget, setPointerTarget] = useState<number | null>(null)
  const [copyIndex, setCopyIndex] = useState(0)
  const [frameIndex, setFrameIndex] = useState(0)
  const { state, pawVisible, paused, reducedMotion, gazeDirection, triggerReaction } = useColliePortraitState(
    thinking,
    isTyping,
    activeAgents,
    pointerTarget
  )
  const frames = portraitFramesFor(state, gazeDirection)
  const frame = frames[frameIndex % frames.length]
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

  useEffect(() => {
    setFrameIndex(0)
    if (paused || reducedMotion || frames.length < 2) return
    const timer = window.setInterval(
      () => setFrameIndex((current) => (current + 1) % frames.length),
      PORTRAIT_FRAME_DURATION[state]
    )
    return () => window.clearInterval(timer)
  }, [frames.length, paused, reducedMotion, state])

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
        data-gaze-direction={gazeDirection ?? 'idle'}
        onPointerMove={handlePointerMove}
        onPointerLeave={() => setPointerTarget(null)}
        onClick={triggerReaction}
        onKeyDown={handleKeyDown}
      >
        <span className="collie-ring-background" aria-hidden="true" />
        <div className="collie-portrait-clip">
          <ColliePortraitFrame
            className="collie-portrait-base"
            frame={frame}
            fallbackSrc={PORTRAIT_STATIC_FALLBACK}
          />
        </div>
        <span className="collie-ring-foreground" aria-hidden="true" />
        <img
          className={`collie-portrait-paw${pawVisible ? ' is-visible' : ''}`}
          src={pawFront}
          alt=""
          draggable={false}
        />
      </div>
      <div className="collie-portrait-status" role="status" aria-live="polite">
        <span key={status}>{status}</span>
        {elapsedLabel ? <small>{elapsedLabel}</small> : null}
      </div>
      {activeAgents.length > 0 && (
        <div className="portrait-agent-list" aria-label={`${activeAgents.length} supporting agents`}>
          {activeAgents.slice(0, 3).map((agent) => (
            <span key={agent.id} title={`${agent.name} · ${agent.phase}`}>
              <AgentAvatar identity={agent.id || agent.name} name={agent.name} size={34} />
              <b>{agent.name}</b>
              <small>{conciseTask(agent)}</small>
            </span>
          ))}
        </div>
      )}
    </section>
  )
}
