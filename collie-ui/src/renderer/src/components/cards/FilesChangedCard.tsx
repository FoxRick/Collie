import { ChevronDown, FileDiff } from 'lucide-react'

export type ChangedFileStatus = 'added' | 'modified' | 'deleted' | 'renamed'

export interface ChangedFile {
  path: string
  additions: number
  deletions: number
  status: ChangedFileStatus
}

interface FilesChangedCardData {
  files: ChangedFile[]
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

/**
 * Cards arrive from a streamed runtime boundary, so accept only the small
 * declared shape. Invalid file entries are ignored; invalid counts become 0.
 */
export function parseFilesChangedCardData(data: Record<string, unknown>): FilesChangedCardData {
  if (!Array.isArray(data.files)) return { files: [] }

  const files = data.files.flatMap((entry): ChangedFile[] => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return []
    const candidate = entry as Record<string, unknown>
    const path = typeof candidate.path === 'string' ? candidate.path.trim() : ''
    if (!path) return []

    return [{
      path,
      additions: asSafeCount(candidate.additions),
      deletions: asSafeCount(candidate.deletions),
      status: asStatus(candidate.status)
    }]
  })

  return { files }
}

function statusLabel(status: ChangedFileStatus): string {
  return status === 'added' ? 'Added'
    : status === 'deleted' ? 'Deleted'
      : status === 'renamed' ? 'Renamed'
        : 'Modified'
}

export default function FilesChangedCard({ data }: { data: Record<string, unknown> }): React.JSX.Element | null {
  const { files } = parseFilesChangedCardData(data)
  if (files.length === 0) return null

  const additions = files.reduce((total, file) => total + file.additions, 0)
  const deletions = files.reduce((total, file) => total + file.deletions, 0)
  const fileLabel = `${files.length} changed file${files.length === 1 ? '' : 's'}`

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
        </div>
      </details>
    </section>
  )
}
