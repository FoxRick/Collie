import { useEffect, useRef, useState, type ReactElement } from 'react'
import { collieClient } from '../lib/ipc'
import type { BrowserCommand, BrowserResult, BrowserSession } from '../../../shared/browser'

export default function BrowserPanel({ conversationId }: { conversationId: string | null }): ReactElement | null {
  const [expanded, setExpanded] = useState(false)
  const [sessions, setSessions] = useState<BrowserSession[]>([])
  const [selected, setSelected] = useState('')
  const [url, setUrl] = useState('')
  const [ref, setRef] = useState('')
  const [text, setText] = useState('')
  const [result, setResult] = useState<BrowserResult>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    setSessions([]); setSelected(''); setResult({}); setError(''); setBusy(false)
    setText(''); setRef(''); setUrl(''); setExpanded(false)
  }, [conversationId])
  if (!conversationId) return null
  const action = async (command: BrowserCommand, file?: 'upload' | 'download'): Promise<void> => {
    const current = ++generation.current
    setBusy(true); setError('')
    try {
      const response = file
        ? await window.collie.browserFile(conversationId, command, file)
        : await collieClient.command<BrowserResult>('browser_action', { conversation_id: conversationId, command }, 360_000)
      if (generation.current !== current) return
      if (response) {
        setResult(response); setRef('')
        if (response.session_id) setSelected(response.session_id)
      }
      const list = await collieClient.command<BrowserResult>('browser_action', {
        conversation_id: conversationId, command: { operation: 'list' }
      })
      if (generation.current !== current) return
      setSessions(list.sessions || [])
      if (response?.closed) setSelected('')
    } catch (cause) {
      if (generation.current === current) {
        const message = cause instanceof Error ? cause.message : 'I could not reach the browser.'
        setError(message.replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, ''))
      }
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  const invoke = (operation: BrowserCommand['operation'], extra: Partial<BrowserCommand> = {}): void => {
    void action({ operation, session_id: selected, url, ref, observation_id: result.observation_id, text, ...extra })
    if (operation === 'fill') setText('')
  }
  const fileAction = (direction: 'upload' | 'download'): void => {
    void action({ operation: 'observe', session_id: selected, ref, observation_id: result.observation_id }, direction)
  }
  const handedOff = sessions.find(s => s.session_id === selected)?.handed_off
  const element = result.elements?.find(e => e.ref === ref)
  return <section aria-label="Browser" style={{ padding: '8px 16px', maxHeight: 320, overflow: 'auto', flexShrink: 0, minWidth: 0 }}>
    <button type="button" onClick={() => { setExpanded(!expanded); if (!expanded) invoke('list') }}>Browser · preview</button>
    {expanded && <div>
      <p>A separate Edge window. Actions ask permission. Take over to sign in, then Resume with the visible site's address. No browser service account needed.</p>
      <input aria-label="Browser address" placeholder="https://example.com" value={url} onChange={e => setUrl(e.target.value)} />
      <button disabled={busy} onClick={() => invoke('open')}>Open browser</button>
      <select aria-label="Browser session" value={selected} onChange={e => { setSelected(e.target.value); setResult({}); setRef('') }}>
        <option value="">Choose browser</option>
        {sessions.map(s => <option key={s.session_id} value={s.session_id}>{s.origin}{s.handed_off ? ' · yours' : ''}</option>)}
      </select>
      <button disabled={busy} onClick={() => invoke('list')}>Refresh browsers</button>
      <button disabled={!selected} onClick={() => invoke('close')}>Close / stop browser</button>
      <button disabled={!selected || handedOff} onClick={() => invoke('handoff')}>Take over</button>
      <button disabled={busy || !selected} onClick={() => invoke('resume')}>Resume with approval</button>
      <fieldset disabled={busy || !selected || handedOff}>
        <legend>Use the page</legend>
        <button onClick={() => invoke('approve_destination')}>Approve destination</button>
        <button onClick={() => invoke('navigate')}>Go to address</button>
        <button onClick={() => invoke('new_tab')}>Open address in new tab</button>
        <button onClick={() => invoke('close_tab')}>Close current tab</button>
        {result.tabs?.map(tab => <button key={tab.tab_id} disabled={tab.active} onClick={() => invoke('tab', { tab_id: tab.tab_id })}>{tab.origin}{tab.active ? ' · current' : ''}</button>)}
        <button onClick={() => invoke('observe')}>Read page</button>
        <button onClick={() => invoke('scroll', { direction: 'up' })}>Scroll up</button>
        <button onClick={() => invoke('scroll', { direction: 'down' })}>Scroll down</button>
        <button onClick={() => invoke('screenshot')}>Capture for me</button>
        <label>Choose a page control
          <select aria-label="Page control" value={ref} onChange={e => setRef(e.target.value)}>
            <option value="">Read page, then choose a control</option>
            {result.elements?.map(e => <option key={e.ref} value={e.ref}>{e.label || e.tag} ({e.role || e.type || e.tag})</option>)}
          </select>
        </label>
        {element?.options && <p>Options: {element.options.join(', ')}</p>}
        <input aria-label="Browser text" autoComplete="off" placeholder="Text or option label; no secrets" value={text} onChange={e => setText(e.target.value)} />
        <button disabled={!ref} onClick={() => invoke('fill')}>Fill field</button>
        <button disabled={!ref} onClick={() => invoke('select')}>Select option</button>
        <button disabled={!ref} onClick={() => invoke('check', { checked: true })}>Check</button>
        <button disabled={!ref} onClick={() => invoke('check', { checked: false })}>Uncheck</button>
        <button disabled={!ref} onClick={() => invoke('click')}>Click</button>
        <button disabled={!ref} onClick={() => invoke('key', { key: 'Tab' })}>Next field</button>
        <button disabled={!ref} onClick={() => invoke('submit')}>Submit / final action…</button>
        <button disabled={!ref} onClick={() => fileAction('upload')}>Choose file to upload…</button>
        <button disabled={!ref} onClick={() => fileAction('download')}>Save public download…</button>
      </fieldset>
      {busy && <p role="status">Waiting for approval or browser… Stop remains available.</p>}
      {error && <p role="alert">{error}</p>}
      {result.message && <p role="status">{result.message}</p>}
      {result.truncated && <p>This page is large. Only a bounded portion is shown.</p>}
      {result.text && <pre style={{ whiteSpace: 'pre-wrap' }}>{result.text}</pre>}
      {result.screenshot && <img src={result.screenshot} alt="Browser capture with input fields masked; for your inspection" style={{ maxWidth: '100%' }} />}
    </div>}
  </section>
}
