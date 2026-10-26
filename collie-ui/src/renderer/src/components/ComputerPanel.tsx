import { useEffect, useRef, useState, type ReactElement } from 'react'
import { collieClient } from '../lib/ipc'

interface WindowInfo { pid: number; window_id: number; title: string; app_name: string }
interface ElementInfo { element_token: string; name?: string; label?: string; role?: string; value?: string }
interface Observation { observation_id?: string; elements?: ElementInfo[]; screenshot?: string; window_title?: string }
interface Result extends Observation {
  windows?: WindowInfo[]; target?: { pid: number; window_id: number; scope: string } | null
  postcondition?: Observation; message?: string; stopped?: boolean; busy?: boolean
}

export default function ComputerPanel({ conversationId }: { conversationId: string | null }): ReactElement | null {
  const [expanded, setExpanded] = useState(false)
  const [windows, setWindows] = useState<WindowInfo[]>([])
  const [selected, setSelected] = useState('')
  const [result, setResult] = useState<Observation>({})
  const [token, setToken] = useState('')
  const [text, setText] = useState('')
  const [keys, setKeys] = useState('ctrl,a')
  const [coordinates, setCoordinates] = useState('')
  const [foreground, setForeground] = useState(false)
  const [desktop, setDesktop] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  useEffect(() => {
    generation.current++
    setExpanded(false); setWindows([]); setSelected(''); setResult({}); setToken('')
    setText(''); setMessage(''); setBusy(false); setDesktop(false); setCoordinates(''); setForeground(false)
  }, [conversationId])
  if (!conversationId) return null
  const action = async (operation: string, screenshot = false): Promise<void> => {
    const current = ++generation.current
    setBusy(true); setMessage('')
    const window = windows.find(w => `${w.pid}:${w.window_id}` === selected)
    const command: Record<string, unknown> = { operation }
    if (!['windows', 'apps', 'stop', 'status'].includes(operation)) {
      if (window && !desktop) { command.pid = window.pid; command.window_id = window.window_id }
      command.scope = desktop ? 'desktop' : 'window'
      command.screenshot = screenshot
      if (!['observe', 'desktop'].includes(operation)) {
        command.observation_id = result.observation_id
        command.delivery_mode = foreground ? 'foreground' : 'background'
        if (token) command.element_token = token
        if (coordinates) {
          const [x, y, toX, toY] = coordinates.split(',').map(Number)
          command.x = x; command.y = y
          if (operation === 'drag') { command.to_x = toX; command.to_y = toY }
        }
        if (operation === 'type') { command.text = text; setText('') }
        if (operation === 'hotkey') command.keys = keys.split(',').map(k => k.trim())
      }
    }
    try {
      const response = await collieClient.command<Result>('computer_action', { conversation_id: conversationId, command }, 360_000)
      if (current !== generation.current) return
      if (response.windows) setWindows(response.windows)
      if (response.observation_id || response.postcondition) { setResult(response.postcondition || response); setToken(''); setCoordinates('') }
      if (response.stopped) { setResult({}); setToken(''); setCoordinates('') }
      setMessage(response.message || (response.target ? `Controlling process ${response.target.pid}, window ${response.target.window_id} (${response.target.scope})` : 'Ready.'))
    } catch (cause) {
      if (current === generation.current) setMessage(cause instanceof Error ? cause.message : 'Computer control could not finish.')
    } finally { if (current === generation.current) setBusy(false) }
  }
  return <section aria-label="Computer control" style={{ padding: '8px 16px', maxHeight: 360, overflow: 'auto', flexShrink: 0 }}>
    <button onClick={() => { setExpanded(!expanded); if (!expanded) void action('status') }}>Computer · local preview</button>
    {expanded && <div>
      <p>Windows accessibility runs locally. Every action asks permission. Select the exact app; use Stop / take over before working in it yourself. Never enter secrets.</p>
      <button onClick={() => void action('stop')}>Stop / take over</button>
      <button disabled={busy} onClick={() => void action('status')}>Show controlled target</button>
      <fieldset disabled={busy}>
        <button onClick={() => void action('windows')}>Find open windows</button>
        <select aria-label="Computer window" value={selected} onChange={e => { setSelected(e.target.value); setResult({}); setToken(''); setDesktop(false) }}>
          <option value="">Choose an app window</option>
          {windows.map(w => <option key={`${w.pid}:${w.window_id}`} value={`${w.pid}:${w.window_id}`}>{w.app_name} · {w.title} · {w.window_id}</option>)}
        </select>
        <label><input type="checkbox" checked={desktop} onChange={e => { setDesktop(e.target.checked); setResult({}); setToken('') }} />Whole desktop (capture and clicks)</label>
        <label><input type="checkbox" checked={foreground} onChange={e => setForeground(e.target.checked)} />Allow foreground for this action</label>
        <button disabled={!selected || desktop} onClick={() => void action('observe')}>Read controls</button>
        <button disabled={!selected && !desktop} onClick={() => void action(desktop ? 'desktop' : 'observe', true)}>Capture chosen target</button>
        {result.window_title && <p>Controlled window: {result.window_title}</p>}
        <div aria-label="Computer elements">{(result.elements || []).map(e => <button key={e.element_token} aria-pressed={token === e.element_token}
          onClick={() => { setToken(e.element_token); setCoordinates('') }}>{e.name || e.label || e.role || e.element_token}{e.value ? `: ${e.value}` : ''}</button>)}</div>
        <input aria-label="Computer text" autoComplete="off" value={text} placeholder="Ordinary text only" onChange={e => setText(e.target.value)} />
        <input aria-label="Computer hotkey" value={keys} onChange={e => setKeys(e.target.value)} />
        <input aria-label="Capture coordinates" value={coordinates} placeholder="Capture pixels: x,y or drag x,y,to x,to y" onChange={e => { setCoordinates(e.target.value); setToken('') }} />
        {['click', 'type', 'hotkey', 'scroll', 'drag'].map(op => <button key={op} disabled={!result.observation_id} onClick={() => void action(op)}>{op}</button>)}
      </fieldset>
      {busy && <p role="status">Waiting for approval or the app… Stop remains available.</p>}
      {message && <p role="status">{message}</p>}
      {result.screenshot && <img src={result.screenshot} alt="Explicitly requested computer capture" style={{ maxWidth: '100%' }} />}
    </div>}
  </section>
}
