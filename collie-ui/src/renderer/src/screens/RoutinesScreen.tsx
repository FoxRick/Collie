import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Clock3,
  History,
  Pause,
  Pencil,
  Play,
  Plus,
  RotateCcw,
  Rocket,
  Settings2,
  Trash2,
  Undo2,
  X
} from 'lucide-react'
import {
  collieClient,
  type CollieAutomation as LoopItem,
  type CollieRun
} from '../lib/ipc'

const LOOP_STARTERS = [
  'Every weekday at 8am, give me a short weather, calendar, and priorities briefing.',
  'Every Friday at 5pm, help me review the week and plan something enjoyable.',
  'On the first day of every month at 9am, remind me to review my budget.'
]

/** Built-in system-maintenance routines shown in their own quiet group. */
const SYSTEM_IDS = new Set(['collie-memory-maintenance', 'collie-gardener-suggestions'])

function friendlySchedule(schedule: string | undefined): string {
  const raw = (schedule || '').trim()
  if (!raw) return ''
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const parts = raw.split(/\s+/)
  const clock = (t: string): string => {
    const [h, m] = t.split(':').map((n) => parseInt(n, 10))
    if (Number.isNaN(h)) return t
    const suffix = h < 12 ? 'am' : 'pm'
    const hour = h % 12 === 0 ? 12 : h % 12
    return m ? `${hour}:${String(m).padStart(2, '0')} ${suffix}` : `${hour} ${suffix}`
  }
  if (parts.length === 1) return `Every day at ${clock(parts[0])}`
  if (parts.length === 2 && /^\d+$/.test(parts[0])) return `Day ${parseInt(parts[0], 10)} of the month at ${clock(parts[1])}`
  if (parts.length === 2 && days.includes(parts[0])) return `${dayName(parts[0])}s at ${clock(parts[1])}`
  return raw
}

function dayName(code: string): string {
  return ({ Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday', Sun: 'Sunday' } as Record<string, string>)[code] || code
}

export default function RoutinesScreen(): React.JSX.Element {
  const [loops, setLoops] = useState<LoopItem[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [history, setHistory] = useState<Record<string, CollieRun[]>>({})
  // Edit flow: which routine is open, plus the text being edited.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  // Post-create confirmation: the freshly created routine, kept for one-tap Undo.
  const [justCreated, setJustCreated] = useState<{ id: string; name: string } | null>(null)

  const refresh = async (): Promise<void> => {
    if (new URLSearchParams(window.location.search).has('preview')) {
      setLoading(false)
      return
    }
    try {
      const data = await collieClient.listRoutines()
      setLoops(data.routines)
    } catch {
      setNotice('I could not check your routines just yet.')
    } finally {
      setLoading(false)
    }
  }

  const refreshRef = useRef(refresh)
  refreshRef.current = refresh

  useEffect(() => {
    void refreshRef.current()
  }, [])

  // Live refresh: routine runs and their steps change while running.
  useEffect(() => {
    return collieClient.on((event) => {
      if (
        event.type === 'routine_updated' ||
        event.type === 'run_started' ||
        event.type === 'run_completed' ||
        event.type === 'run_failed' ||
        event.type === 'run_step_updated'
      ) {
        void refreshRef.current()
      }
    })
  }, [])

  const toggle = async (loop: LoopItem): Promise<void> => {
    const enabled = loop.enabled !== 1
    setBusy(true)
    try {
      if (enabled) {
        const result = await collieClient.resumeRoutine(loop.id)
        setLoops((current) =>
          current.map((item) => (item.id === loop.id ? result.routine : item))
        )
      } else {
        const result = await collieClient.pauseRoutine(loop.id)
        setLoops((current) =>
          current.map((item) => (item.id === loop.id ? result.routine : item))
        )
      }
      setNotice(enabled ? `${loop.name} is enabled.` : `${loop.name} is paused.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not update that routine.')
    } finally {
      setBusy(false)
    }
  }

  const createLoop = async (): Promise<void> => {
    if (!description.trim()) return
    setBusy(true)
    try {
      const result = await collieClient.createAutomation(
        description.trim(),
        undefined,
        Intl.DateTimeFormat().resolvedOptions().timeZone
      )
      setDescription('')
      setCreating(false)
      await refresh()
      // Show what Collie understood, with a one-tap Undo — free-text schedule
      // parsing is magic until the user sees the interpretation.
      setJustCreated({ id: result.automation.id, name: result.automation.name })
      setNotice('')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not create that routine.')
    } finally {
      setBusy(false)
    }
  }

  const undoCreate = async (): Promise<void> => {
    if (!justCreated) return
    const target = justCreated
    setJustCreated(null)
    setBusy(true)
    try {
      await collieClient.deleteAutomation(target.id)
      await refresh()
      setNotice('Undone — that routine was removed.')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not remove that routine.')
    } finally {
      setBusy(false)
    }
  }

  const startEdit = (loop: LoopItem): void => {
    setEditingId(loop.id)
    setEditText(loop.description || '')
  }

  const saveEdit = async (): Promise<void> => {
    if (!editingId || !editText.trim()) return
    setBusy(true)
    try {
      await collieClient.updateAutomation(
        editingId,
        editText.trim(),
        undefined,
        Intl.DateTimeFormat().resolvedOptions().timeZone
      )
      setEditingId(null)
      setEditText('')
      await refresh()
      setNotice('Routine updated.')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not update that routine.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (loop: LoopItem): Promise<void> => {
    if (!window.confirm(`Delete ${loop.name}?`)) return
    setBusy(true)
    try {
      await collieClient.deleteAutomation(loop.id)
      await refresh()
      setNotice(`${loop.name} was deleted.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not delete that routine.')
    } finally {
      setBusy(false)
    }
  }

  const runNow = async (loop: LoopItem): Promise<void> => {
    setBusy(true)
    try {
      await collieClient.runRoutineNow(loop.id)
      setNotice(`${loop.name} is running now.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not start that routine.')
    } finally {
      setBusy(false)
    }
  }

  const toggleHistory = async (loop: LoopItem): Promise<void> => {
    if (history[loop.id]) {
      setHistory((current) => {
        const next = { ...current }
        delete next[loop.id]
        return next
      })
      return
    }
    const data = await collieClient.listRoutineRuns(loop.id)
    setHistory((current) => ({ ...current, [loop.id]: data.runs }))
  }

  /** A paused card has no honest "Next" until it runs again. */
  const nextRunLabel = (loop: LoopItem): string => {
    if (loop.enabled !== 1) return 'Paused'
    if (!loop.next_run_at) return 'Not scheduled'
    return new Date(loop.next_run_at).toLocaleString()
  }

  /** Missed-window skips are normal life (computer off), not errors. */
  const runLine = (loop: LoopItem, run: CollieRun): React.JSX.Element => (
    <div key={run.id}>
      {run.status === 'skipped' && (run as { error_code?: string }).error_code === 'missed_window' ? (
        <>
          <strong>Missed</strong>
          <span>{run.scheduled_for ? new Date(run.scheduled_for).toLocaleString() : run.trigger_type}</span>
          <small>Your computer was likely off — nothing was lost.</small>
        </>
      ) : (
        <>
          <strong>{run.status}</strong>
          <span>{run.started_at ? new Date(run.started_at).toLocaleString() : run.trigger_type}</span>
          {run.error_message ? <small>{run.error_message}</small> : null}
        </>
      )}
      {run.status === 'failed' ? (
        <button
          className="routine-retry"
          onClick={() => void (async () => {
            await collieClient.retryRoutineRun(run.id)
            setNotice('Retry started.')
            const data = await collieClient.listRoutineRuns(loop.id)
            setHistory((current) => ({ ...current, [loop.id]: data.runs }))
          })()}
        >
          <RotateCcw size={11} /> Retry
        </button>
      ) : null}
    </div>
  )

  const userLoops = useMemo(() => loops.filter((loop) => !SYSTEM_IDS.has(loop.id)), [loops])
  const systemLoops = useMemo(() => loops.filter((loop) => SYSTEM_IDS.has(loop.id)), [loops])

  const renderCard = (loop: LoopItem): React.JSX.Element => (
    <article key={loop.id} className={`loop-card ${loop.enabled === 1 ? '' : 'is-paused'}`}>
      <div className="loop-icon"><Clock3 size={20} /></div>
      <div className="loop-copy">
        <div className="loop-title-row">
          <h2>{loop.name}</h2>
          <span className={`loop-state ${loop.enabled === 1 ? 'is-running' : ''}`}>
            {loop.routine_status === 'needs_attention'
              ? 'Needs attention'
              : loop.enabled === 1 ? 'Enabled' : 'Paused'}
          </span>
        </div>
        {loop.description && <p>{loop.description}</p>}
        <div className="loop-schedule"><Clock3 size={13} /> {friendlySchedule(loop.schedule) || 'Schedule set by Collie'}</div>
        <div className="routine-facts">
          <span>Next: {nextRunLabel(loop)}</span>
          <span>Last success: {loop.last_success_at ? new Date(loop.last_success_at).toLocaleString() : 'Never'}</span>
          {loop.last_failure_at ? <span>Last failure: {new Date(loop.last_failure_at).toLocaleString()}</span> : null}
          {loop.plan_version ? <span>Plan: v{loop.plan_version}</span> : null}
        </div>
        {history[loop.id] ? (
          <div className="routine-history">
            {history[loop.id].length ? history[loop.id].slice(0, 5).map((run) => runLine(loop, run)) : <span>No runs yet.</span>}
          </div>
        ) : null}
      </div>
      <div className="loop-actions">
        <button
          type="button"
          className="secondary-button"
          onClick={() => void runNow(loop)}
          disabled={busy || (loop.action_type === 'approved_plan' && !loop.plan_version)}
        >
          <Rocket size={14} /> Run now
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={() => void toggleHistory(loop)}
          disabled={busy}
        >
          <History size={14} /> History
        </button>
        <button
          type="button"
          className="secondary-button"
          role="switch"
          aria-checked={loop.enabled === 1}
          onClick={() => void toggle(loop)}
          disabled={busy}
        >
          {loop.enabled === 1 ? <Pause size={14} /> : <Play size={14} />}
          {loop.enabled === 1 ? 'Pause' : 'Resume'}
        </button>
        {!loop.id.startsWith('collie-') && (
          <>
            <button
              type="button"
              className="icon-button loop-edit"
              aria-label={`Edit ${loop.name}`}
              title="Edit this routine"
              onClick={() => startEdit(loop)}
              disabled={busy}
            >
              <Pencil size={15} />
            </button>
            <button
              type="button"
              className="icon-button loop-delete"
              aria-label={`Delete ${loop.name}`}
              onClick={() => void remove(loop)}
              disabled={busy}
            >
              <Trash2 size={15} />
            </button>
          </>
        )}
      </div>
    </article>
  )

  return (
    <main className="section-workspace flex min-w-0 flex-1 flex-col overflow-hidden">
      <header className="section-header">
        <div>
          <h1>Routines</h1>
          <p>Put tasks on repeat — see what ran and fix misses.</p>
        </div>
        <button type="button" className="primary-button" onClick={() => setCreating(true)}>
          <Plus size={16} /> Create routine
        </button>
      </header>

      <div className="section-scroll">
        {notice && <p className="inline-notice" role="status">{notice}</p>}
        {justCreated && (
          <div className="routine-created-banner" role="status">
            <span>
              Collie will repeat: <strong>{justCreated.name}</strong>
            </span>
            <button type="button" className="secondary-button routine-undo" onClick={() => void undoCreate()} disabled={busy}>
              <Undo2 size={13} /> Undo
            </button>
          </div>
        )}
        {loading ? (
          <div className="section-loading">Checking your routines...</div>
        ) : loops.length > 0 ? (
          <>
            {userLoops.length > 0 ? (
              <div className="loop-list">{userLoops.map(renderCard)}</div>
            ) : null}
            {systemLoops.length > 0 ? (
              <>
                <h3 className="routine-group-label"><Settings2 size={13} /> System maintenance</h3>
                <div className="loop-list">{systemLoops.map(renderCard)}</div>
              </>
            ) : null}
          </>
        ) : (
          <div className="loops-empty">
            <span className="section-placeholder-icon"><Clock3 size={25} /></span>
            <h2>Put an approved plan on repeat</h2>
            <p>Describe what Collie should do and when. Plain language works best.</p>
            <div className="loop-starters">
              {LOOP_STARTERS.map((starter) => (
                <button
                  key={starter}
                  type="button"
                  onClick={() => {
                    setDescription(starter)
                    setCreating(true)
                  }}
                >
                  {starter}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {creating && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="dialog-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-loop-title"
          >
            <div className="dialog-heading">
              <div>
                <span className="detail-label">NEW ROUTINE</span>
                <h2 id="new-loop-title">What should Collie repeat?</h2>
              </div>
              <button type="button" className="icon-button" onClick={() => setCreating(false)} aria-label="Close">
                <X size={17} />
              </button>
            </div>
            <label className="form-field">
              <span>Describe the task and schedule</span>
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={5}
                placeholder="Every weekday at 8am, brief me on today's weather, calendar, and priorities."
                autoFocus
              />
            </label>
            <p className="dialog-hint">Include both what you want and when it should happen.</p>
            <div className="dialog-actions">
              <button type="button" className="secondary-button" onClick={() => setCreating(false)}>Cancel</button>
              <button
                type="button"
                className="primary-button"
                disabled={busy || !description.trim()}
                onClick={() => void createLoop()}
              >
                {busy ? 'Setting the schedule...' : 'Create routine'}
              </button>
            </div>
          </section>
        </div>
      )}

      {editingId && (
        <div className="dialog-backdrop" role="presentation">
          <section
            className="dialog-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="edit-loop-title"
          >
            <div className="dialog-heading">
              <div>
                <span className="detail-label">EDIT ROUTINE</span>
                <h2 id="edit-loop-title">Change what Collie repeats</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => {
                  setEditingId(null)
                  setEditText('')
                }}
                aria-label="Close"
              >
                <X size={17} />
              </button>
            </div>
            <label className="form-field">
              <span>Describe the task and schedule</span>
              <textarea
                value={editText}
                onChange={(event) => setEditText(event.target.value)}
                rows={5}
                placeholder="Every weekday at 8am, brief me on today's weather, calendar, and priorities."
                autoFocus
              />
            </label>
            <p className="dialog-hint">
              Reword it freely — its history and pause state stay put.
            </p>
            <div className="dialog-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setEditingId(null)
                  setEditText('')
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={busy || !editText.trim()}
                onClick={() => void saveEdit()}
              >
                {busy ? 'Saving...' : 'Save changes'}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
