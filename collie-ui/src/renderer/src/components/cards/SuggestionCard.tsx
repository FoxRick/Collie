import { useEffect, useState } from 'react'
import { Check, Undo2, X, FileText, Sparkles } from 'lucide-react'
import { collieClient } from '../../lib/ipc'
import DiffView from './DiffView'

interface Props {
  data: Record<string, unknown>
}

/** Merge a suggestion into existing file content — never overwrite. */
export function mergeSuggestion(existing: string, suggestion: string): string {
  const trimmed = suggestion.trim()
  if (!trimmed) return existing
  const current = existing.trim()
  if (!current) return `${trimmed}\n`
  return `${current}\n\n${trimmed}\n`
}

type Status = 'pending' | 'checking' | 'saving' | 'saved' | 'dismissed'

export default function SuggestionCard({ data }: Props): React.JSX.Element {
  const file = String(data.file || '')
  const label = String(data.label || '')
  const suggestion = String(data.suggestion || '')
  const reasoning = String(data.reasoning || '')
  const fileName = file || 'AGENTS.md'

  const [status, setStatus] = useState<Status>('checking')
  const [busy, setBusy] = useState(false)
  const [versionId, setVersionId] = useState<string | null>(null)
  const [diffText, setDiffText] = useState<string | null>(null)
  const [undoNotice, setUndoNotice] = useState('')

  const isPersonality = file === 'VISION.md'
  const Icon = isPersonality ? Sparkles : FileText

  useEffect(() => {
    let cancelled = false
    collieClient.readFile(fileName).then(({ content }) => {
      if (cancelled) return
      if (content.trim() === suggestion.trim()) {
        setStatus('saved')
      } else {
        setStatus('pending')
      }
    }).catch(() => {
      if (!cancelled) setStatus('pending')
    })
    return () => { cancelled = true }
  }, [fileName, suggestion])

  const handleApprove = async (): Promise<void> => {
    setStatus('saving')
    setBusy(true)
    setUndoNotice('')
    try {
      const { content } = await collieClient.readFile(fileName)
      if (content.trim() === suggestion.trim()) {
        setStatus('saved')
        return
      }
      // Merge, never overwrite: the target file may hold user-authored
      // content the suggestion must not destroy.
      const applied = mergeSuggestion(content, suggestion)
      const result = await collieClient.writeFile(fileName, applied)
      setVersionId(result.version_id ?? null)
      setDiffText(result.diff_text ?? null)
      setStatus('saved')
    } catch {
      setStatus('pending')
    } finally {
      setBusy(false)
    }
  }

  const handleUndo = async (): Promise<void> => {
    if (!versionId) return
    setBusy(true)
    try {
      await collieClient.rollbackArtifact(versionId)
      setVersionId(null)
      setDiffText(null)
      setUndoNotice('Undone — the change was rolled back.')
      setStatus('pending')
    } catch (error) {
      setUndoNotice(error instanceof Error ? error.message : 'I could not undo that change.')
      setStatus('saved')
    } finally {
      setBusy(false)
    }
  }

  const handleDismiss = (): void => {
    setStatus('dismissed')
  }

  if (status === 'checking') return <div />

  if (status === 'saved') {
    return (
      <div className="suggestion-card saved">
        <div className="suggestion-card-header">
          <span className="suggestion-card-icon saved-icon">
            <Check size={16} />
          </span>
          <span className="suggestion-card-label">Applied to {label}</span>
        </div>
        <p className="suggestion-card-text">{reasoning}</p>
        {diffText && <DiffView diff={diffText} label="See what changed" />}
        {undoNotice && <p className="suggestion-card-text">{undoNotice}</p>}
        {versionId && (
          <div className="suggestion-card-actions">
            <button
              type="button"
              className="suggestion-btn undo"
              onClick={() => void handleUndo()}
              disabled={busy}
            >
              <Undo2 size={14} /> {busy ? 'Undoing…' : 'Undo'}
            </button>
          </div>
        )}
      </div>
    )
  }

  if (status === 'dismissed') {
    return (
      <div className="suggestion-card dismissed">
        <div className="suggestion-card-header">
          <span className="suggestion-card-icon dismissed-icon">
            <X size={16} />
          </span>
          <span className="suggestion-card-label">Dismissed</span>
        </div>
      </div>
    )
  }

  return (
    <div className="suggestion-card">
      <div className="suggestion-card-header">
        <span className="suggestion-card-icon">
          <Icon size={16} />
        </span>
        <span className="suggestion-card-label">Edit {label}</span>
      </div>
      <p className="suggestion-card-reason">{reasoning}</p>
      <div className="suggestion-card-preview">
        <pre className="suggestion-card-preview-text">{suggestion}</pre>
      </div>
      <div className="suggestion-card-actions">
        <button
          className="suggestion-btn approve"
          onClick={() => void handleApprove()}
          disabled={status === 'saving'}
        >
          {status === 'saving' ? 'Applying…' : 'Approve edit'}
        </button>
        <button
          className="suggestion-btn dismiss"
          onClick={handleDismiss}
          disabled={status === 'saving'}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
