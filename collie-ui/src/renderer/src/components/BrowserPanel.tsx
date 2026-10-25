import { useEffect, useRef, useState, type ReactElement } from 'react'
import { collieClient } from '../lib/ipc'
import type { BrowserCommand, BrowserResult, BrowserSession } from '../../../shared/browser'

export default function BrowserPanel({ conversationId }: { conversationId: string | null }): ReactElement | null {
  const [expanded, setExpanded] = useState(false)
  const [sessions, setSessions] = useState<BrowserSession[]>([])
  const [selected, setSelected] = useState('')
  const [url, setUrl] = useState('')
  const [selector, setSelector] = useState('')
  const [text, setText] = useState('')
  const [result, setResult] = useState<BrowserResult>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    setSessions([]); setSelected(''); setResult({}); setError(''); setBusy(false)
    setText(''); setSelector(''); setUrl(''); setExpanded(false)
  }, [conversationId])
  if (!conversationId) return null
  const action = async (command: BrowserCommand): Promise<void> => {
    const current = ++generation.current
    setBusy(true); setError('')
    try {
      const response = await collieClient.command<BrowserResult>('browser_action', {
        conversation_id: conversationId, command
      }, 360_000)
      if (generation.current !== current) return
      setResult(response)
      if (response.session_id) setSelected(response.session_id)
      const list = await collieClient.command<BrowserResult>('browser_action', {
        conversation_id: conversationId, command: { operation: 'list' }
      })
      if (generation.current !== current) return
      setSessions(list.sessions || [])
      if (response.closed) setSelected('')
    } catch (cause) {
      if (generation.current === current) setError(cause instanceof Error ? cause.message : 'I could not reach the browser.')
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  const invoke = (operation: BrowserCommand['operation']): void => {
    void action({ operation, session_id: selected, url, selector, text })
    if (operation === 'fill') setText('')
  }
  const handedOff = sessions.find(s => s.session_id === selected)?.handed_off
  return <section aria-label="Browser" style={{ padding: '8px 16px', maxHeight: 400, overflow: 'auto' }}>
    <button type="button" onClick={() => { setExpanded(!expanded); if (!expanded) invoke('list') }}>Browser · preview</button>
    {expanded && <div>
      <p>A separate visible Edge window. Each action asks permission. Use “Take over” for sign-in or final actions; close it when finished. Requires the optional browser runtime.</p>
      <input aria-label="Browser address" placeholder="https://example.com" value={url} onChange={e => setUrl(e.target.value)} />
      <button disabled={busy} onClick={() => invoke('open')}>Open browser</button>
      <select aria-label="Browser session" value={selected} onChange={e => { setSelected(e.target.value); setResult({}) }}>
        <option value="">Choose browser</option>
        {sessions.map(s => <option key={s.session_id} value={s.session_id}>{s.origin} · {s.session_id.slice(0, 6)}{s.handed_off ? ' · yours' : ''}</option>)}
      </select>
      <button disabled={busy} onClick={() => invoke('list')}>Refresh</button>
      <button disabled={!selected} onClick={() => invoke('close')}>Close / stop browser</button>
      <button disabled={busy || !selected || handedOff} onClick={() => invoke('handoff')}>Take over</button>
      <fieldset disabled={busy || !selected || handedOff}>
        <button onClick={() => invoke('navigate')}>Navigate</button>
        <button onClick={() => invoke('observe')}>Read page</button>
        <button onClick={() => invoke('screenshot')}>Screenshot</button>
        <input aria-label="Browser element" placeholder="CSS selector" value={selector} onChange={e => setSelector(e.target.value)} />
        <input aria-label="Browser text" autoComplete="off" placeholder="Ordinary text only; no secrets" value={text} onChange={e => setText(e.target.value)} />
        <button onClick={() => invoke('fill')}>Fill field</button>
        <button onClick={() => invoke('click')}>Click element</button>
      </fieldset>
      {busy && <p role="status">Waiting for approval or browser…</p>}
      {error && <p role="alert">{error}</p>}
      {result.message && <p role="status">{result.message}</p>}
      {result.text && <pre style={{ whiteSpace: 'pre-wrap' }}>{result.text}</pre>}
      {result.elements && <div aria-label="Page elements">
        {result.elements.map(element => <button key={element.selector} disabled={busy || handedOff}
          onClick={() => setSelector(element.selector)}>{element.label || element.tag}</button>)}
      </div>}
      {result.screenshot && <img src={result.screenshot} alt="Browser capture with input fields masked" style={{ maxWidth: '100%' }} />}
    </div>}
  </section>
}
