import { useState } from 'react'
import { Check, Sparkles, Undo2, X } from 'lucide-react'
import { collieClient, type GardenerSuggestion } from '../../lib/ipc'
import DiffView from './DiffView'

interface Props {
  data: Record<string, unknown>
}

type Status = 'pending' | 'applying' | 'applied' | 'dismissed'

const TYPE_LABELS: Record<string, string> = {
  subagent: 'a subagent',
  agents: 'Collie’s instructions (AGENTS.md)',
  vision: 'Collie’s personality (VISION.md)',
  memory_dream: 'long-term memory'
}

/** One Gardener suggestion: evidence + proposed change + Approve/Dismiss. */
export default function GardenerCard({ data }: Props): React.JSX.Element {
  const suggestions = (data.suggestions ?? []) as GardenerSuggestion[]
  if (suggestions.length === 0) return <div />

  return (
    <div className="gardener-card">
      <div className="suggestion-card-heading">
        <Sparkles size={15} />
        <span className="suggestion-card-label">Collie’s improvement ideas</span>
      </div>
      <p className="suggestion-card-text">
        I looked at recent run records and found a few small things worth
        reviewing. Approve any you like — everything stays undoable.
      </p>
      {suggestions.map((suggestion, index) => (
        <GardenerRow
          key={`${suggestion.artifact_type}:${suggestion.artifact_key}:${index}`}
          suggestion={suggestion}
        />
      ))}
    </div>
  )
}

function GardenerRow({
  suggestion
}: {
  suggestion: GardenerSuggestion
}): React.JSX.Element {
  const [status, setStatus] = useState<Status>('pending')
  const [busy, setBusy] = useState(false)
  const [versionId, setVersionId] = useState<string | null>(null)
  const [diffText, setDiffText] = useState<string | null>(null)
  const [notice, setNotice] = useState('')

  const label = TYPE_LABELS[suggestion.artifact_type] ?? suggestion.artifact_key
  const fileName =
    suggestion.artifact_type === 'subagent'
      ? `subagents/${suggestion.artifact_key}`
      : suggestion.artifact_key

  const handleApprove = async (): Promise<void> => {
    setStatus('applying')
    setBusy(true)
    setNotice('')
    try {
      const result = await collieClient.applyGardenerSuggestion(suggestion)
      setVersionId(result.version_id ?? null)
      setDiffText(result.diff_text ?? null)
      setStatus('applied')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not apply that change.')
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
      setNotice('Undone — the change was rolled back.')
      setStatus('pending')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'I could not undo that change.')
    } finally {
      setBusy(false)
    }
  }

  const handleDismiss = (): void => {
    setStatus('dismissed')
  }

  return (
    <div className="gardener-row">
      <div className="gardener-row-head">
        <b>Improve {label}</b>
        <span className="gardener-file">{fileName}</span>
      </div>
      {suggestion.rationale && (
        <p className="suggestion-card-text">{suggestion.rationale}</p>
      )}
      {suggestion.evidence_ids.length > 0 && (
        <p className="gardener-evidence">
          Based on: {suggestion.evidence_ids.join(' · ')}
        </p>
      )}
      {status === 'dismissed' ? (
        <p className="suggestion-card-text">Dismissed — nothing changed.</p>
      ) : status === 'applied' ? (
        <>
          <p className="suggestion-card-text">Applied. You can undo it any time.</p>
          {diffText && <DiffView diff={diffText} label="See what changed" />}
          {notice && <p className="suggestion-card-text">{notice}</p>}
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
        </>
      ) : (
        <>
          <pre className="gardener-proposed">{suggestion.proposed_text}</pre>
          {notice && <p className="suggestion-card-text">{notice}</p>}
          <div className="suggestion-card-actions">
            <button
              type="button"
              className="suggestion-btn approve"
              onClick={() => void handleApprove()}
              disabled={busy || status === 'applying'}
            >
              <Check size={14} /> {status === 'applying' ? 'Applying…' : 'Approve'}
            </button>
            <button
              type="button"
              className="suggestion-btn dismiss"
              onClick={handleDismiss}
              disabled={busy}
            >
              <X size={14} /> Dismiss
            </button>
          </div>
        </>
      )}
    </div>
  )
}
