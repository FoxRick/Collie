import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ActiveAgent, ThinkingState } from '../lib/ipc'
import {
  portraitStateForEngine,
  supportsFaceOnlyDeepWork,
  type PortraitState
} from './portraitStates'

const DEEP_WORK_DELAY_MS = 11_000
const COMPLETION_DURATION_MS = 2_400
const CLICK_REACTION_DURATION_MS = 850
const CLICK_COOLDOWN_MS = 1_000

interface PortraitModel {
  state: PortraitState
  paused: boolean
  reducedMotion: boolean
  triggerReaction: () => void
}

export function useColliePortraitState(
  thinking: ThinkingState | null,
  isTyping: boolean,
  activeAgents: ActiveAgent[],
  pointerTarget: number | null
): PortraitModel {
  const [transient, setTransient] = useState<PortraitState | null>(null)
  const [sleepy, setSleepy] = useState(false)
  const [paused, setPaused] = useState(document.hidden)
  const [reducedMotion, setReducedMotion] = useState(() =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
  const [deepWorkReady, setDeepWorkReady] = useState(false)
  const clickCooldownUntil = useRef(0)
  const transientTimer = useRef<number | null>(null)

  const engineState = portraitStateForEngine(thinking?.state)
  const isAssistantWorking = engineState === 'working'
  const isActive = isAssistantWorking || engineState === 'review' || isTyping || activeAgents.length > 0

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
    setSleepy(false)
    if (isActive) return
    const timer = window.setTimeout(() => setSleepy(true), 20_000)
    return () => window.clearTimeout(timer)
  }, [isActive])

  useEffect(() => {
    setDeepWorkReady(false)
    if (!isAssistantWorking || !supportsFaceOnlyDeepWork()) return
    const timer = window.setTimeout(() => setDeepWorkReady(true), DEEP_WORK_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [isAssistantWorking])

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
    },
    []
  )

  const state = useMemo<PortraitState>(() => {
    // Priority matches the approved interaction model: hard states, then
    // short reactions, then work, pointer attention, and finally idle.
    if (thinking?.state === 'error') return 'error'
    if (engineState === 'waiting') return 'waiting'
    if (transient) return transient
    // Gate on the current base state as well as the timer so glasses disappear
    // in the same render that work ends, before the reset effect runs.
    if (deepWorkReady && isAssistantWorking) return 'deep_work_glasses'
    if (engineState) return engineState
    if (isTyping || activeAgents.length > 0) return 'working'
    if (!reducedMotion && pointerTarget !== null) return 'pointer_look'
    return sleepy ? 'sleepy' : 'idle'
  }, [deepWorkReady, engineState, isAssistantWorking, pointerTarget, reducedMotion, sleepy, thinking?.state, transient])

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
    triggerReaction
  }
}
