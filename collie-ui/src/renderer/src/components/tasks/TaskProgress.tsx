import { Check, ChevronDown, Circle, CircleAlert, CircleDot, CircleX, LoaderCircle, PauseCircle, Square } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import type { TaskState, TaskStep } from '../../lib/ipc'

const terminalStatuses = new Set(['completed', 'blocked', 'cancelled', 'failed', 'stopped'])

export function isTaskTerminal(task: TaskState): boolean {
  return terminalStatuses.has(task.status)
}

function statusLabel(status: TaskStep['status']): string {
  return status.replace('_', ' ')
}

function StatusIcon({ status }: { status: TaskStep['status'] }): React.JSX.Element {
  const props = { size: 16, 'aria-hidden': true }
  switch (status) {
    case 'completed':
      return <Check {...props} />
    case 'in_progress':
      return <LoaderCircle {...props} className="animate-spin" />
    case 'blocked':
      return <CircleAlert {...props} />
    case 'failed':
      return <CircleX {...props} />
    case 'skipped':
      return <PauseCircle {...props} />
    default:
      return <Circle {...props} />
  }
}

function ElapsedTime({ task }: { task: TaskState }): React.JSX.Element | null {
  const [now, setNow] = useState(Date.now)
  const terminal = isTaskTerminal(task)
  useEffect(() => {
    if (terminal || !task.created_at) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [task.created_at, terminal])

  const start = task.created_at ? Date.parse(task.created_at) : Number.NaN
  const end = task.completed_at ? Date.parse(task.completed_at) : now
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  const seconds = Math.max(1, Math.floor((end - start) / 1000))
  const label = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return <span className="text-xs" style={{ color: 'var(--collie-text-muted)' }}>{label}</span>
}

function currentLabel(task: TaskState): string {
  if (task.status === 'completed') return 'Task complete'
  if (task.status === 'blocked') return 'Task needs your input'
  if (task.status === 'failed') return 'Task could not finish'
  if (task.status === 'stopped' || task.status === 'cancelled') return 'Task stopped'
  const current = task.steps.find((step) => step.key === task.current_step_key)
    ?? task.steps.find((step) => step.status === 'in_progress')
  if (current) return current.title
  return task.title
}

function currentStep(task: TaskState): TaskStep | undefined {
  return task.steps.find((step) => step.key === task.current_step_key)
    ?? task.steps.find((step) => step.status === 'in_progress')
    ?? task.steps.find((step) => step.status === 'blocked' || step.status === 'failed')
}

function progressLabel(task: TaskState, current: TaskStep | undefined): string {
  if (task.status === 'completed') return `Completed ${task.total_count} of ${task.total_count}`
  const stepNumber = current ? task.steps.findIndex((step) => step.key === current.key) + 1 : 0
  return stepNumber > 0 ? `Step ${stepNumber} of ${task.total_count}` : `${task.completed_count} of ${task.total_count} complete`
}

interface Props {
  task: TaskState
  onStop?: () => void
  /** History summaries remain compact and cannot issue task controls. */
  readOnly?: boolean
}

/** Compact, accessible user-facing progress for one conversation only. */
export default function TaskProgress({ task, onStop, readOnly = false }: Props): React.JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const terminal = isTaskTerminal(task)
  const current = currentStep(task)
  const progress = progressLabel(task, current)

  const statusText = `${progress}. ${currentLabel(task)}.`
  return (
    <section
      className="mx-auto mb-2 rounded-xl border bg-[var(--collie-surface)] shadow-sm"
      style={{ borderColor: 'var(--collie-border)', width: 'min(calc(100% - 24px), 920px)' }}
      aria-label="Task progress"
    >
      {readOnly ? (
        <div className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm" style={{ color: 'var(--collie-text)' }}>
          <CircleDot size={16} aria-hidden="true" style={{ color: 'var(--collie-grass)' }} />
          <span className="min-w-0 flex-1 truncate font-medium">{currentLabel(task)}</span>
          <span className="shrink-0 text-xs" style={{ color: 'var(--collie-text-muted)' }}>{progress}</span>
        </div>
      ) : (
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm hover:bg-[var(--collie-bone)] focus-visible:outline-2 focus-visible:outline-offset-2"
        style={{ color: 'var(--collie-text)' }}
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => setExpanded((value) => !value)}
      >
        <CircleDot size={16} aria-hidden="true" style={{ color: 'var(--collie-grass)' }} />
        <span className="min-w-0 flex-1 truncate font-medium">{currentLabel(task)}</span>
        <span className="shrink-0 text-xs" style={{ color: 'var(--collie-text-muted)' }}>{progress}</span>
        <ChevronDown size={16} aria-hidden="true" className={expanded ? 'rotate-180' : ''} />
      </button>
      )}
      {!expanded && !readOnly ? <span role="status" aria-live="polite" aria-atomic="true" className="sr-only">{statusText}</span> : null}
      {expanded && !readOnly ? (
        <div id={detailsId} className="border-t px-3 pb-3 pt-2" style={{ borderColor: 'var(--collie-border)' }}>
          <div className="mb-2 flex items-center justify-between gap-3">
            <div>
              <p className="m-0 text-sm font-semibold">{task.title}</p>
              <p className="m-0 text-xs" style={{ color: 'var(--collie-text-muted)' }}>
                {progress} - {currentLabel(task)} <ElapsedTime task={task} />
              </p>
            </div>
            {!terminal && onStop ? (
              <button type="button" className="secondary-button inline-flex items-center gap-1" onClick={onStop}>
                <Square size={13} aria-hidden="true" /> Stop
              </button>
            ) : null}
          </div>
          <ol className="m-0 grid list-none gap-2 p-0">
            {task.steps.map((step) => (
              <li
                key={step.key}
                className={`flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm ${step.key === current?.key ? 'bg-[var(--collie-bone)]' : ''}`}
                aria-current={step.key === current?.key ? 'step' : undefined}
              >
                <span className="mt-0.5 shrink-0" aria-hidden="true"><StatusIcon status={step.status} /></span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={step.key === current?.key ? 'font-semibold' : undefined}>{step.title}</span>
                    <span className="text-xs capitalize" style={{ color: 'var(--collie-text-muted)' }}>{statusLabel(step.status)}</span>
                  </div>
                  {(step.summary || step.error_message) ? (
                    <p className="m-0 text-xs" style={{ color: 'var(--collie-text-muted)' }}>
                      {step.error_message || step.summary}
                    </p>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  )
}
