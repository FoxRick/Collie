import { useEffect, useMemo, useState } from 'react'
import type { ActiveAgent, ThinkingState } from '../lib/ipc'
import { portraitStateForEngine, type PortraitState } from './portraitStates'

interface PortraitModel {
  state: PortraitState
  pawVisible: boolean
  paused: boolean
}

export function useColliePortraitState(
  thinking: ThinkingState | null,
  isTyping: boolean,
  activeAgents: ActiveAgent[],
  hovered: boolean
): PortraitModel {
  const [transient, setTransient] = useState<PortraitState | null>(null)
  const [sleepy, setSleepy] = useState(false)
  const [paused, setPaused] = useState(document.hidden)

  useEffect(() => {
    const onVisibilityChange = (): void => setPaused(document.hidden)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])

  useEffect(() => {
    setSleepy(false)
    const engineIsActive =
      Boolean(thinking) && !['idle', 'done', 'error'].includes(thinking?.state || '')
    if (engineIsActive || isTyping || activeAgents.length > 0) return
    const timer = window.setTimeout(() => setSleepy(true), 20_000)
    return () => window.clearTimeout(timer)
  }, [activeAgents.length, isTyping, thinking])

  useEffect(() => {
    const timers: number[] = []
    if (thinking?.state === 'done') {
      setTransient('celebrate')
      timers.push(window.setTimeout(() => setTransient('bark'), 900))
      timers.push(window.setTimeout(() => setTransient('happy'), 1220))
      timers.push(window.setTimeout(() => setTransient(null), 3600))
    } else if (thinking?.state === 'error') {
      setTransient('concerned')
      timers.push(window.setTimeout(() => setTransient(null), 1800))
    } else {
      setTransient(null)
    }
    return () => timers.forEach(window.clearTimeout)
  }, [thinking?.state])

  const state = useMemo<PortraitState>(() => {
    if (transient) return transient
    const engineState = portraitStateForEngine(thinking?.state)
    if (engineState) return engineState
    if (isTyping) return 'attentive'
    if (activeAgents.length > 0) return 'listening'
    if (hovered) return 'paw_over_ring'
    return sleepy ? 'sleepy' : 'idle'
  }, [activeAgents.length, hovered, isTyping, sleepy, thinking?.state, transient])

  return {
    state,
    pawVisible: state === 'paw_over_ring' || state === 'happy',
    paused
  }
}
