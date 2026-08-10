import { useState } from 'react'
import { ChevronDown, FileDiff, Undo2 } from 'lucide-react'
import { collieClient } from '../../lib/ipc'

export type ChangedFileStatus = 'added' | 'modified' | 'deleted' | 'renamed'

export interface ChangedFile {
  path: string
  additions: number
  deletions: number
  status: ChangedFileStatus
  /** Shadow-journal entry id; present when this change can be undone. */
  undoEntryId?: string
}

export interface FilesChangedCardData {
  files: ChangedFile[]
  /** Conversation owning the undo journal; required for the undo button. */
  conversationId?: string
}

const supportedStatuses = new Set<ChangedFileStatus>(['added', 'modified', 'deleted', 'renamed'])

function asSafeCount(value: unknown): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : 0
}

function asStatus(value: unknown): ChangedFileStatus {
  return typeof value === 'string' && supportedStatuses.has(value as ChangedFileStatus)
    ? value as ChangedFileStatus
    : 'modified'
}

function asOptionalId(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

/**
 * Cards arrive from a streamed runtime boundary, so accept only the small
 * declared shape. Invalid file entries are ignored; invalid counts become 0.
 */
export function parseFilesChangedCardData(data: Record<string, unknown>): FilesChangedCardData {
  const conversationId = asOptionalId(data.conversation_id)

  if (!Array.isArray(data.files)) return { files: [], conversationId }

  const files = data.files.flatMap((entry): ChangedFile[] => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return []
    const candidate = entry as Record<string, unknown>
    const path = typeof candidate.path === 'string' ? candidate.path.trim() : ''
    if (!path) return []

    return [{
      path,
      additions: asSafeCount(candidate.additions),
      deletions: asSafeCount(candidate.deletions),
      status: asStatus(candidate.status),
      undoEntryId: asOptionalId(candidate.undo_entry_id)
    }]
  })

  return { files, conversationId }
}

function statusLabel(status: ChangedFileStatus): string {
  return status === 'added' ? 'Added'
    : status === 'deleted' ? 'Deleted'
      : status === 'renamed' ? 'Renamed'
        : 'Modified'
}

type UndoState = 'idle' | 'undoing' | 'undone' | 'error'

export default function FilesChangedCard({ data }: { data: Record<string, unknown> }): React.JSX.Element | null {
  const { files, conversationId } = parseFilesChangedCardData(data)
  const [undoState, setUndoState] = useState<UndoState>('idle')
  const [notice, setNotice] = useState('')

  if (files.length === 0) return null

  const additions = files.reduce((total, file) => total + file.additions, 0)
  const deletions = files.reduce((total, file) => total + file.deletions, 0)
  const fileLabel = `${files.length} changed file${files.length === 1 ? '' : 's'}`
  const undoable = files.some((file) => file.undoEntryId !== undefined) && conversationId !== undefined
  const busy = undoState === 'undoing'

  const handleUndo = async (): Promise<void> => {
    if (!conversationId || busy || undoState === 'undone') return
    setUndoState('undoing')
    setNotice('')
    try {
      const result = await collieClient.undoFileChanges(
        conversationId,
        files.flatMap((file) => (file.undoEntryId ? [file.undoEntryId] : []))
      )
      if (result.errors.length > 0) {
        setNotice('Some files could not be restored. They may have been moved or deleted since.')
        setUndoState('error')
        return
      }
      setUndoState('undone')
      setNotice('Undone — the files are back the way they were.')
    } catch {
      setNotice('I could not undo that change. Try again?')
      setUndoState('error')
    }
  }

  return (
    <section aria-label="Files changed" className="my-2 max-w-2xl rounded-xl border border-stone-200 bg-white text-stone-900 shadow-sm">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 marker:content-none">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-stone-100 text-stone-600"><FileDiff size={16} /></span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">{fileLabel}</span>
            <span className="mt-0.5 flex gap-2 text-xs font-medium tabular-nums">
              <span className="text-emerald-700">+{additions}</span>
              <span className="text-rose-700">-{deletions}</span>
            </span>
          </span>
          <ChevronDown aria-hidden="true" className="text-stone-400 transition-transform group-open:rotate-180" size={16} />
        </summary>
        <div className="border-t border-stone-100 px-3 py-2">
          <ul className="space-y-1" aria-label="Changed files">
            {files.map((file, index) => (
              <li className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-stone-50" key={`${file.path}-${index}`}>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-stone-700" title={file.path}>{file.path}</span>
                <span className="hidden rounded bg-stone-100 px-1.5 py-0.5 text-[10px] font-medium text-stone-500 sm:inline">{statusLabel(file.status)}</span>
                <span className="shrink-0 text-xs font-medium tabular-nums text-emerald-700">+{file.additions}</span>
                <span className="shrink-0 text-xs font-medium tabular-nums text-rose-700">-{file.deletions}</span>
              </li>
            ))}
          </ul>
          {undoable && (
            <div className="mt-2 border-t border-stone-100 pt-2" aria-label="Undo changes">
              {undoState === 'undone' ? (
                <p className="text-xs font-medium text-emerald-700">✓ {notice}</p>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleUndo()}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-stone-900 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-stone-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Undo2 size={13} /> {busy ? 'Taking it back…' : 'Take it back'}
                </button>
              )}
              {notice && undoState !== 'undone' && (
                <p className="mt-1.5 text-xs text-rose-700">{notice}</p>
              )}
            </div>
          )}
        </div>
      </details>
    </section>
  )
}
