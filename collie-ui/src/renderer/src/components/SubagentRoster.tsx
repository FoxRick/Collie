import { useEffect, useState } from 'react'
import { Ban, Check, CircleX } from 'lucide-react'
import type { ActiveAgent } from '../lib/ipc'
import {
  agentActivityLine,
  agentElapsedMs,
  agentOutcomeLabel,
  formatAgentElapsed
} from '../lib/agentActivity'
import AgentAvatar from './AgentAvatar'

const MAX_WORKING_ROWS = 6
const MAX_SETTLED_ROWS = 6

function OutcomeIcon({ outcome }: { outcome: ActiveAgent['outcome'] }): React.JSX.Element {
  if (outcome === 'error') return <CircleX size={14} className="agent-outcome-icon is-error" aria-hidden />
  if (outcome === 'cancelled') return <Ban size={14} className="agent-outcome-icon is-cancelled" aria-hidden />
  return <Check size={14} className="agent-outcome-icon is-ok" aria-hidden />
}

function RosterRow({
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
    <div
      className={settled ? 'roster-row is-settled' : 'roster-row'}
      title={agent.task_description || undefined}
    >
      <span className="roster-row-avatar">
        <AgentAvatar identity={agent.id || agent.name} name={agent.name} size={34} />
        {!settled && <span className="agent-live-dot" aria-hidden />}
      </span>
      <span className="roster-row-copy">
        <b>{agent.name}</b>
        <small>
          {settled ? (
            <>
              <OutcomeIcon outcome={agent.outcome} />
              {agentOutcomeLabel(agent.outcome)}
            </>
          ) : (
            agentActivityLine(agent)
          )}
        </small>
      </span>
      <em className="roster-row-elapsed">{elapsedLabel}</em>
    </div>
  )
}

interface Props {
  active: ActiveAgent[]
  recent: ActiveAgent[]
  /** Test seam: inject a fixed "now" (defaults to a live 1 s tick). */
  nowMs?: number
}

/**
 * Live subagent roster for the Agents tab: "Working now" on top, settled
 * "Earlier" rows below, agent definitions render underneath (AgentsScreen).
 * Pure presentational — the screen owns the poll.
 */
export default function SubagentRoster({ active, recent, nowMs }: Props): React.JSX.Element | null {
  const [now, setNow] = useState(nowMs ?? Date.now)
  const hasLive = active.length > 0 || recent.length > 0

  useEffect(() => {
    if (nowMs !== undefined) return
    if (!hasLive) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [hasLive, nowMs])

  if (!hasLive) return null

  const working = active.slice(0, MAX_WORKING_ROWS)
  const workingOverflow = active.length - working.length
  const settled = recent.slice(0, MAX_SETTLED_ROWS)

  return (
    <section className="subagent-roster" aria-label="Agents activity">
      {active.length > 0 && (
        <div className="roster-group">
          <div className="roster-heading">
            <span className="roster-heading-dot" aria-hidden />
            Working now
            {active.length > 1 ? ` · ${active.length}` : ''}
          </div>
          <div className="roster-list">
            {working.map((agent) => (
              <RosterRow key={`w-${agent.id}`} agent={agent} now={now} settled={false} />
            ))}
            {workingOverflow > 0 && (
              <div className="roster-overflow">+{workingOverflow} more working</div>
            )}
          </div>
        </div>
      )}
      {settled.length > 0 && (
        <div className="roster-group">
          <div className="roster-heading">Earlier</div>
          <div className="roster-list">
            {settled.map((agent) => (
              <RosterRow key={`s-${agent.id}`} agent={agent} now={now} settled />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
