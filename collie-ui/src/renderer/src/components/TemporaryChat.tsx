import { useEffect, useRef, useState } from 'react'
import { collieClient } from '../lib/ipc'

export default function TemporaryChat({ onClose }: { onClose: () => void }): React.JSX.Element {
  const session = useRef<string | null>(null)
  const [ready, setReady] = useState(false)
  const [messages, setMessages] = useState<Array<{ role: string; text: string }>>([])
  const [draft, setDraft] = useState('')
  const [stream, setStream] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let disposed = false
    void collieClient.command<{ session_id: string }>('temporary_open').then(({ session_id }) => {
      if (disposed) {
        void collieClient.command('temporary_close', { session_id }).catch(() => {})
      } else {
        session.current = session_id
        setReady(true)
      }
    }).catch(() => setError("I couldn't open a temporary chat. Try again?"))
    const off = collieClient.on((event) => {
      if (event.type === 'connection_opened') {
        // Reconnection cannot establish ownership of the old in-memory session.
        session.current = null
        setMessages([])
        setStream('')
        setBusy(false)
        setReady(false)
        setError('The connection changed. Close this chat and open a new temporary chat.')
      }
      if ((event.type !== 'temporary_delta' && event.type !== 'temporary_done') ||
          event.session_id !== session.current) return
      if (event.type === 'temporary_delta') setStream((text) => text + event.text)
      else {
        setMessages((items) => [...items, { role: 'Collie', text: event.content }])
        setStream('')
        setBusy(false)
      }
    })
    return () => {
      disposed = true
      off()
      const session_id = session.current
      session.current = null
      if (session_id) void collieClient.command('temporary_close', { session_id }).catch(() => {})
    }
  }, [])

  const send = async (): Promise<void> => {
    const content = draft.trim()
    if (!content || !ready || busy) return
    setMessages((items) => [...items, { role: 'You', text: content }])
    setDraft('')
    setStream('')
    setError('')
    setBusy(true)
    try {
      await collieClient.command('temporary_turn', { session_id: session.current, content })
    } catch {
      setBusy(false)
      setError("I couldn't start that reply. Try again?")
    }
  }

  return <main className="workspace flex min-w-0 flex-1 flex-col p-6" aria-label="Temporary chat">
    <header className="flex items-center justify-between">
      <h1>Temporary chat</h1>
      <button type="button" onClick={onClose}>Close and forget</button>
    </header>
    <p className="my-3 text-sm text-muted">
      This text chat stays in memory until you close it. It does not enter saved history,
      personal memory, search, exports, or self-review. Your model provider receives your
      messages and its retention policy still applies. Tools, plans, routines, helpers,
      attachments, and sharing are unavailable here. Existing profile context may be read.
    </p>
    <div className="min-h-0 flex-1 overflow-auto" aria-live="polite">
      {messages.map((message, index) => <div key={index} className="my-4 whitespace-pre-wrap">
        <strong>{message.role}</strong><p>{message.text}</p>
      </div>)}
      {busy && <p className="whitespace-pre-wrap">{stream || 'Thinking…'}</p>}
    </div>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={(event) => { event.preventDefault(); void send() }} className="flex gap-3">
      <textarea aria-label="Temporary message" value={draft} maxLength={100000}
        onChange={(event) => setDraft(event.target.value)} className="min-w-0 flex-1 p-3" />
      {busy ? <button type="button" onClick={() => {
        void collieClient.command('temporary_stop', { session_id: session.current })
          .then(() => { setBusy(false); setStream('') })
          .catch(() => setError("I couldn't stop that reply. Close this chat to forget it."))
      }}>Stop</button> : <button type="submit" disabled={!ready || !draft.trim()}>Send</button>}
    </form>
  </main>
}
