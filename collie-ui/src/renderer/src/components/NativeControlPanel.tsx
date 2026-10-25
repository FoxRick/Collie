import { useState } from 'react'
import { collieClient, type NativeWindow } from '../lib/ipc'

export default function NativeControlPanel({ conversationId }: { conversationId: string }): React.JSX.Element {
  const [windows, setWindows] = useState<NativeWindow[]>([])
  const [notice, setNotice] = useState('')
  const [selected, setSelected] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async (operation: 'discover' | 'select' | 'observe' | 'focus' | 'scroll' | 'stop', target?: NativeWindow): Promise<void> => {
    setBusy(true)
    try {
      const result = await collieClient.nativeControl(conversationId, operation, { target, scope: 'unsaved_editor' })
      if (operation === 'discover') {
        setWindows(result.windows || [])
        setNotice(result.windows?.length ? 'Choose the window you want Collie to use.' : 'No supported window found. Open an unsaved classic Notepad window titled Untitled - Notepad.')
      } else if (operation === 'select') {
        setSelected(true)
        setNotice('Selected for this chat: unsaved editor only. Ask Collie to replace text, focus or scroll. Each action needs approval.')
      } else if (operation === 'stop') {
        setSelected(false)
        setNotice('Native control stopped. Select a window again to continue.')
      } else {
        setNotice(result.stopped ? 'Native control stopped.' : 'Done. Observation confirms editor availability; document text is never captured.')
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }
  return <details className="mx-4 my-2 rounded border p-2 text-sm">
    <summary>Use a local Notepad window (experimental)</summary>
    <p>Classic unsaved English Notepad only. No menus, saving, sending, screenshots or document reading. Typing replaces all editor text. Some Windows versions are unsupported.</p>
    <button disabled={busy} onClick={() => void run('discover')}>Find supported windows</button>
    {windows.map(window => <button key={`${window.hwnd}:${window.started}`} disabled={busy} onClick={() => void run('select', window)}>
      Select {window.title} (window {window.hwnd})
    </button>)}
    {selected && <>
      <button disabled={busy} onClick={() => void run('observe')}>Check editor</button>
      <button disabled={busy} onClick={() => void run('focus')}>Focus editor</button>
      <button disabled={busy} onClick={() => void run('scroll')}>Scroll down</button>
    </>}
    <button onClick={() => void run('stop')}>Stop native control</button>
    <p role="status">{notice}</p>
  </details>
}
