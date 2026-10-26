import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ActiveAgent, ThinkingState } from '../lib/ipc'
import { portraitStateForEngine, type PortraitState } from './portraitStates'

const SLEEP_DELAY_MS = 180_000
const COMPLETION_DURATION_MS = 2_400
const CLICK_REACTION_DURATION_MS = 2_100
const CLICK_COOLDOWN_MS = 1_000

interface PortraitModel {
  state: PortraitState
  paused: boolean
  reducedMotion: boolean
  triggerReaction: () => void
  triggerHappyReaction: () => void
}

export function useColliePortraitState(
  thinking: ThinkingState | null,
  isTyping: boolean,
  activeAgents: ActiveAgent[]
): PortraitModel {
  const [transient, setTransient] = useState<PortraitState | null>(null)
  const [paused, setPaused] = useState(document.hidden)
  const [reducedMotion, setReducedMotion] = useState(() =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
  const [asleep, setAsleep] = useState(false)
  const [handoff, setHandoff] = useState(false)
  const previousAgents = useRef(new Set<string>())
  const handoffTimer = useRef<number | null>(null)
  const clickCooldownUntil = useRef(0)
  const transientTimer = useRef<number | null>(null)

  const engineState = portraitStateForEngine(thinking?.state)
  const busy = !!engineState || activeAgents.length > 0 || isTyping

  const setTimedTransient = useCallback((next: PortraitState, duration: number): void => {
    if (transientTimer.current !== null) window.clearTimeout(transientTimer.current)
    setTransient(next)
    transientTimer.current = window.setTimeout(() => {
      setTransient((current) => (current === next ? null : current))
      transientTimer.current = null
    }, duration)
  }, [])

  useEffect(() => {
    const onVisibilityChange = (): void => setPaused(document.hidden)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (): void => setReducedMotion(query.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    setAsleep(false)
    if (busy || transient || thinking?.state === 'error') return
    const timer = window.setTimeout(() => setAsleep(true), SLEEP_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [busy, transient, thinking?.state])

  useEffect(() => {
    const ids = new Set(activeAgents.map(agent => agent.id))
    const added = [...ids].some(id => !previousAgents.current.has(id))
    previousAgents.current = ids
    if (!added) return
    setHandoff(true)
    if (handoffTimer.current !== null) window.clearTimeout(handoffTimer.current)
    handoffTimer.current = window.setTimeout(() => setHandoff(false), 1700)
  }, [activeAgents])

  useEffect(() => {
    if (engineState !== 'waiting') return
    if (transientTimer.current !== null) {
      window.clearTimeout(transientTimer.current)
      transientTimer.current = null
    }
    setTransient(null)
  }, [engineState])

  useEffect(() => {
    if (thinking?.state === 'done') setTimedTransient('completion', COMPLETION_DURATION_MS)
    else if (thinking?.state === 'error') setTimedTransient('error', 1800)
  }, [setTimedTransient, thinking?.state])

  useEffect(
    () => () => {
      if (transientTimer.current !== null) window.clearTimeout(transientTimer.current)
      if (handoffTimer.current !== null) window.clearTimeout(handoffTimer.current)
    },
    []
  )

  const state = useMemo<PortraitState>(() => {
    // Blocking states stay readable; pointer location has no role.
    if (thinking?.state === 'error') return 'error'
    if (engineState === 'waiting') return 'waiting'
    if (transient) return transient
    if (handoff && activeAgents.length > 0) return 'delegating'
    if (engineState) return engineState
    if (activeAgents.length > 0) return 'listening'
    if (isTyping) return 'attentive'
    if (asleep) return 'sleepy'
    return 'idle'
  }, [activeAgents.length, asleep, engineState, handoff, isTyping, thinking?.state, transient])

  const triggerReaction = useCallback(() => {
    if (state === 'error' || state === 'waiting' || state === 'completion') return
    const now = Date.now()
    if (now < clickCooldownUntil.current) return
    clickCooldownUntil.current = now + CLICK_COOLDOWN_MS
    setTimedTransient('click_reaction', CLICK_REACTION_DURATION_MS)
  }, [setTimedTransient, state])

  return {
    state,
    paused,
    reducedMotion,
    triggerReaction,
    triggerHappyReaction: () => {
      if (state === 'error' || state === 'waiting' || state === 'completion') return
      // A double click promotes the first click's wave even during cooldown.
      setTimedTransient('happy_reaction', 2_400)
    }
  }
}
