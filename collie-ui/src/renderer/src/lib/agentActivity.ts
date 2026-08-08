import type { ActiveAgent, SubagentOutcome } from './ipc'

/**
 * How long a settled subagent row stays visible next to the pet / in the
 * roster (ms). Shared by both surfaces so the expiry behavior is one
 * decision, not two.
 */
export const SETTLED_VISIBILITY_MS = 3 * 60_000

/** Friendly labels for the engine's subagent lifecycle phases. */
export const AGENT_PHASE_LABELS: Record<string, string> = {
  initializing: 'Getting ready',
  awaiting_tools: 'Using tools',
  tools_completed: 'Putting it together',
  final_response: 'Wrapping up',
  done: 'Done',
  error: 'Had trouble',
  cancelled: 'Stopped'
}

export function agentPhaseLabel(agent: Pick<ActiveAgent, 'phase'>): string {
  return AGENT_PHASE_LABELS[agent.phase] || agent.phase || 'Working'
}

/** One-line activity for a roster/popup row: task first, phase as fallback. */
export function agentActivityLine(agent: ActiveAgent): string {
  const source = (
    agent.task_description ||
    agentPhaseLabel(agent) ||
    'Working'
  ).replace(/\s+/g, ' ').trim()
  const sentence = source.split(/(?<=[.!?])\s/)[0]
  return sentence.length > 72 ? `${sentence.slice(0, 69).trimEnd()}…` : sentence
}

export const AGENT_OUTCOME_LABELS: Record<SubagentOutcome, string> = {
  ok: 'Done',
  error: 'Couldn’t finish',
  cancelled: 'Stopped'
}

export function agentOutcomeLabel(outcome: SubagentOutcome | undefined): string {
  return outcome ? AGENT_OUTCOME_LABELS[outcome] : 'Done'
}

/** Format a millisecond duration compactly: 42s, 2m 14s, 1h 3m. */
export function formatAgentElapsed(milliseconds: number): string {
  const totalSeconds = Math.max(1, Math.floor(milliseconds / 1000))
  if (totalSeconds < 60) return `${totalSeconds}s`
  const totalMinutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (totalMinutes < 60) return seconds ? `${totalMinutes}m ${seconds}s` : `${totalMinutes}m`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

/**
 * Elapsed time for an agent row in ms. Settled rows freeze at ended_at;
 * working rows tick against the supplied "now".
 */
export function agentElapsedMs(
  agent: ActiveAgent,
  nowMs: number,
  fallbackNowMs = Date.now()
): number | null {
  const start = agent.started_at_ms
  if (typeof start !== 'number' || !Number.isFinite(start)) return null
  const end = agent.ended_at_ms
  if (typeof end === 'number' && Number.isFinite(end)) {
    return Math.max(0, end - start)
  }
  return Math.max(0, (Number.isFinite(nowMs) ? nowMs : fallbackNowMs) - start)
}

export function isAgentSettled(agent: ActiveAgent): boolean {
  return agent.outcome === 'ok' || agent.outcome === 'error' || agent.outcome === 'cancelled'
}

/**
 * Settled rows still inside the visibility window. Rows age out after
 * SETTLED_VISIBILITY_MS from ended_at, and rows without a wall-clock
 * ended_at_ms are dropped — once polling stops, the last snapshot must not
 * linger (or keep a 1 s ticker alive) forever.
 */
export function settledRowsWithinWindow(
  agents: ActiveAgent[],
  nowMs: number
): ActiveAgent[] {
  return agents.filter((agent) => {
    const ended = agent.ended_at_ms
    return typeof ended === 'number' && nowMs - ended < SETTLED_VISIBILITY_MS
  })
}
