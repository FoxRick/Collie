import { AlertTriangle, Check, ShieldCheck, X } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { collieClient, type ApprovalRequest } from '../../lib/ipc'

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true'
  )
}

interface Props {
  approval: ApprovalRequest
  onResolved: () => void
  inline?: boolean
  activeModal?: boolean
}

const FRIENDLY_ACTION_LABELS: Record<string, string> = {
  web_fetch: 'Visit a website',
  web_search: 'Search the web',
  browser_navigate: 'Open a website',
  file_read: 'Read a file',
  file_write: 'Edit a file',
  file_delete: 'Delete a file',
  shell_execute: 'Run a command',
  email_send: 'Send an email',
  message_send: 'Send a message'
}

function actionKey(value: string): string {
  return value
    .trim()
    .replace(/^use\s+/i, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function isTechnicalActionLabel(value: string): boolean {
  return /^use\s+[A-Za-z0-9._:-]+$/i.test(value.trim()) || /^[A-Za-z0-9._:-]+$/.test(value.trim())
}

export function friendlyApprovalLabel(summary: unknown, action: string): string {
  const requestedLabel = typeof summary === 'string' ? summary.trim() : ''
  const requestedKey = actionKey(requestedLabel)
  const actionLabel = action.trim()
  const actionKeyValue = actionKey(actionLabel)

  if (FRIENDLY_ACTION_LABELS[requestedKey]) return FRIENDLY_ACTION_LABELS[requestedKey]
  if (requestedLabel && !isTechnicalActionLabel(requestedLabel)) return requestedLabel
  if (FRIENDLY_ACTION_LABELS[actionKeyValue]) return FRIENDLY_ACTION_LABELS[actionKeyValue]

  const fallback = requestedLabel || actionLabel || 'Continue this action'
  return fallback
    .replace(/^use\s+/i, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[._:-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\b[a-z]/g, (letter) => letter.toUpperCase())
    .trim()
}

export default function ApprovalSheet({
  approval,
  onResolved,
  inline = false,
  activeModal = true
}: Props): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const titleId = useId()
  const detailsId = useId()
  const destinationId = useId()
  const sheetRef = useRef<HTMLElement>(null)
  const display = useMemo(() => {
    try {
      return JSON.parse(approval.display_json) as Record<string, unknown>
    } catch {
      return {}
    }
  }, [approval.display_json])
  const actionLabel = friendlyApprovalLabel(display.summary, approval.action)
  const destination = approval.resource || 'The current task'
  const canGrantRun = Boolean(approval.run_id) && display.approve_for_me_eligible === true

  const resolve = async (
    resolution: 'allow_once' | 'allow_run' | 'allow_scope' | 'reject'
  ): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      await collieClient.resolveApproval(
        approval.id,
        resolution,
        resolution === 'allow_run' ? 'run' : undefined,
        resolution === 'allow_run' ? approval.run_id || undefined : undefined
      )
      onResolved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'That approval is no longer pending.')
    } finally {
      setBusy(false)
    }
  }

  const approveRun = async (): Promise<void> => {
    if (!approval.run_id) return
    setBusy(true)
    setError('')
    try {
      await collieClient.approveAllForRun(approval.run_id)
      await collieClient.resolveApproval(approval.id, 'allow_once')
      onResolved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not approve the run.')
    } finally {
      setBusy(false)
    }
  }

  const isFloatingModal = !inline && activeModal
  const isQueuedFloating = !inline && !activeModal

  useEffect(() => {
    if (!isFloatingModal) return
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const sheet = sheetRef.current
    const firstFocusable = sheet ? focusableElements(sheet)[0] : null
    const focusTarget = firstFocusable ?? sheet
    focusTarget?.focus()
    return () => {
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [isFloatingModal])

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>): void => {
    if (!isFloatingModal) return
    if (event.key === 'Escape' && !busy) {
      event.preventDefault()
      event.stopPropagation()
      void resolve('reject')
      return
    }
    if (event.key !== 'Tab') return
    const sheet = sheetRef.current
    if (!sheet) return
    const focusable = focusableElements(sheet)
    if (focusable.length === 0) {
      event.preventDefault()
      sheet.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const current = document.activeElement
    if (event.shiftKey && (current === first || !sheet.contains(current))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (current === last || !sheet.contains(current))) {
      event.preventDefault()
      first.focus()
    }
  }

  const SheetElement = inline ? 'article' : 'aside'

  return (
    <SheetElement
      ref={sheetRef}
      className={`approval-sheet ${inline ? 'approval-sheet--inline' : 'approval-sheet--floating'}`}
      role={isFloatingModal ? 'dialog' : 'region'}
      aria-modal={isFloatingModal ? true : undefined}
      aria-hidden={isQueuedFloating ? true : undefined}
      aria-labelledby={titleId}
      aria-describedby={destinationId}
      tabIndex={isFloatingModal ? -1 : undefined}
      hidden={isQueuedFloating}
      onKeyDown={handleKeyDown}
    >
      <div className="approval-heading">
        <span><ShieldCheck size={19} /></span>
        <div>
          <div className="detail-label">ACTION APPROVAL</div>
          <h2 id={titleId}>{actionLabel}</h2>
        </div>
      </div>
      <p id={destinationId} className="approval-destination">
        <span>For</span> {destination}
      </p>
      {approval.risk === 'sensitive' || approval.risk === 'destructive' ? (
        <p className="approval-warning"><AlertTriangle size={15} /> This always needs fresh approval.</p>
      ) : null}
      {error ? <p className="approval-error">{error}</p> : null}
      <div className="approval-actions">
        <button disabled={busy} className="secondary-button" onClick={() => void resolve('reject')}>
          <X size={14} /> Reject
        </button>
        {canGrantRun ? (
          <button disabled={busy} className="secondary-button" onClick={() => void resolve('allow_run')}>
            Allow for this run
          </button>
        ) : null}
        <button disabled={busy} className="primary-button" onClick={() => void resolve('allow_once')}>
          <Check size={14} /> Allow once
        </button>
      </div>
      <details className="approval-details" id={detailsId}>
        <summary>Review details</summary>
        <dl>
          <div>
            <dt>Data leaving this computer</dt>
            <dd>
              {Array.isArray(display.data_leaving_device) && display.data_leaving_device.length
                ? display.data_leaving_device.join(', ')
                : 'None'}
            </dd>
          </div>
          <div>
            <dt>Reversible</dt>
            <dd>{display.reversible ? 'Yes' : 'No — review carefully'}</dd>
          </div>
          <div><dt>Why</dt><dd>{String(display.reason || 'Needed for the current step')}</dd></div>
        </dl>
      </details>
      {canGrantRun ? (
        <button className="approval-run-all" disabled={busy} onClick={() => void approveRun()}>
          Approve all ordinary requests for this run
        </button>
      ) : null}
    </SheetElement>
  )
}
