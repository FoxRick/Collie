import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { FEEDBACK_MAX_LENGTH } from '../../../shared/feedback'

export default function FeedbackDialog({ onClose }: { onClose: () => void }): React.JSX.Element {
  const dialog = useRef<HTMLDialogElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const inFlight = useRef(false)
  const submissionId = useRef<string | null>(null)
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.showModal()
    return () => { previous?.focus() }
  }, [])

  useEffect(() => {
    if (sent) closeButton.current?.focus()
  }, [sent])

  async function send(): Promise<void> {
    if (inFlight.current || !message.trim()) return
    inFlight.current = true
    setSending(true)
    setError('')
    try {
      submissionId.current ??= crypto.randomUUID()
      const result = await window.collie.submitFeedback({
        id: submissionId.current,
        message: message.trim()
      })
      if (result.ok) {
        setMessage('')
        setSent(true)
      } else {
        setError(result.error === 'rate_limited'
          ? 'Please wait a minute before trying again. Your message is still here.'
          : "We couldn't confirm delivery. Please try again. Your message is still here.")
      }
    } catch {
      setError("We couldn't confirm delivery. Please try again. Your message is still here.")
    } finally {
      inFlight.current = false
      setSending(false)
    }
  }

  return createPortal(
    <dialog ref={dialog} className="dialog-card feedback-dialog" aria-labelledby="feedback-title"
      aria-describedby="feedback-description" onCancel={(event) => {
        event.preventDefault()
        if (!inFlight.current) onClose()
      }}>
      <form onSubmit={(event) => { event.preventDefault(); void send() }}>
        <h2 id="feedback-title" className="text-lg font-semibold">Submit Feedback</h2>
        <p id="feedback-description" className="dialog-hint">
          Tell us what worked, what didn't, or what you'd like next. Only your message is sent to the Collie team.
        </p>
        {sent ? <p role="status" className="mt-4">Thanks! Your feedback has been sent.</p> : <>
          <label className="form-field" htmlFor="feedback-message">Your feedback
            <textarea id="feedback-message" rows={6} maxLength={FEEDBACK_MAX_LENGTH}
              value={message} disabled={sending} required
              onChange={(event) => {
                setMessage(event.target.value)
                submissionId.current = null
                setError('')
              }} />
          </label>
          <p className="dialog-hint">{message.length} / {FEEDBACK_MAX_LENGTH}</p>
          {error && <p role="alert" className="mt-3 text-sm">{error}</p>}
        </>}
        <div className="dialog-actions">
          <button ref={closeButton} type="button" className="rounded-lg border px-4 py-2 text-sm"
            disabled={sending} onClick={onClose}>{sent ? 'Done' : 'Cancel'}</button>
          {!sent && <button type="submit" disabled={sending || !message.trim()}
            className="rounded-lg bg-black px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
            {sending ? 'Sending…' : 'Send'}
          </button>}
        </div>
      </form>
    </dialog>, document.body
  )
}
