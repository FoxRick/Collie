import { CheckCircle2, ClipboardList, Pencil, Repeat2, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { collieClient } from '../../lib/ipc'
import {
  PLAN_CHANGE_RESULT_EVENT,
  requestPlanChange,
  type PlanChangeResult,
  type PlanChangeState
} from './planChange'

interface PlanStep {
  key: string
  title: string
  description?: string
  risk?: string
  verification?: string
}

interface Props {
  data: Record<string, unknown>
}

export default function PlanCard({ data }: Props): React.JSX.Element {
  const [state, setState] = useState<'ready' | 'starting' | 'started' | 'error'>('ready')
  const [routineState, setRoutineState] = useState<'ready' | 'saving' | 'saved' | 'error'>('ready')
  const [changeState, setChangeState] = useState<PlanChangeState | null>(null)
  const [changeMessage, setChangeMessage] = useState('')
  const changeInFlightRef = useRef(false)
  const plan = (data.plan || {}) as Record<string, unknown>
  const steps = Array.isArray(plan.steps) ? (plan.steps as PlanStep[]) : []
  const planId = String(data.plan_id || '')
  const version = Number(data.version || 0)

  useEffect(() => {
    const receive = (event: Event): void => {
      const detail = (event as CustomEvent<PlanChangeResult>).detail
      if (!detail || detail.planId !== planId || detail.version !== version) return
      setChangeState(detail.state)
      setChangeMessage(detail.message)
      if (detail.state === 'error') changeInFlightRef.current = false
    }
    window.addEventListener(PLAN_CHANGE_RESULT_EVENT, receive)
    return () => window.removeEventListener(PLAN_CHANGE_RESULT_EVENT, receive)
  }, [planId, version])

  const execute = async (): Promise<void> => {
    setState('starting')
    try {
      await collieClient.approvePlan(
        planId,
        version,
        String(data.plan_hash || '')
      )
      setState('started')
    } catch {
      setState('error')
    }
  }

  const saveRoutine = async (): Promise<void> => {
    const schedule = window.prompt(
      'When should this run? For example: weekdays at 8am or every Friday at 5pm'
    )
    if (!schedule?.trim()) return
    setRoutineState('saving')
    try {
      await collieClient.createRoutineFromPlan(
        planId,
        version,
        String(data.plan_hash || ''),
        schedule.trim(),
        Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      )
      setRoutineState('saved')
    } catch {
      setRoutineState('error')
    }
  }

  const changePlan = (): void => {
    if (changeInFlightRef.current) return
    changeInFlightRef.current = true
    setChangeState('requesting')
    setChangeMessage('Asking Collie to pause at a safe boundary…')
    requestPlanChange(planId, version)
  }

  const changeLabel =
    changeState === 'requesting' ? 'Pausing safely…'
      : changeState === 'pending_safe_boundary' ? 'Waiting for a safe pause…'
        : changeState === 'paused' ? 'Describe the changes'
          : changeState === 'error' ? 'Try again'
            : 'Change plan'

  return (
    <article className="plan-card">
      <header>
        <span><ClipboardList size={19} /></span>
        <div>
          <div className="detail-label">PLAN · VERSION {Number(data.version || 1)}</div>
          <h3>{String(plan.title || 'Proposed plan')}</h3>
          <p>{String(plan.goal || '')}</p>
        </div>
      </header>
      <ol>
        {steps.map((step) => (
          <li key={step.key}>
            <CheckCircle2 size={15} />
            <div>
              <strong>{step.title}</strong>
              {step.description ? <span>{step.description}</span> : null}
              <small>{step.risk || 'read'} · {step.verification || 'Verify the result'}</small>
            </div>
          </li>
        ))}
      </ol>
      <div className="plan-guard"><ShieldCheck size={14} /> Actions still follow your approval rules.</div>
      {changeState ? <p className="detail-label" role="status" aria-live="polite">{changeMessage}</p> : null}
      <footer>
        <button
          type="button"
          className="secondary-button"
          disabled={changeState === 'requesting' || changeState === 'pending_safe_boundary' || changeState === 'paused'}
          onClick={changePlan}
        >
          <Pencil size={14} /> {changeLabel}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={routineState === 'saving' || routineState === 'saved'}
          onClick={() => void saveRoutine()}
        >
          <Repeat2 size={14} />
          {routineState === 'saved'
            ? 'Routine saved'
            : routineState === 'error' ? 'Try saving again' : 'Save as routine'}
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={state === 'starting' || state === 'started'}
          onClick={() => void execute()}
        >
          {state === 'started' ? 'Execution started' : state === 'error' ? 'Try again' : 'Approve & execute'}
        </button>
      </footer>
    </article>
  )
}
