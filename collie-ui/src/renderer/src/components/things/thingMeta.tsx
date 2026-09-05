import { File, FileText, Globe, Image as ImageIcon, Table } from 'lucide-react'

const KIND_LABEL_KEY: Record<string, string> = {
  image: 'things.kind.image',
  document: 'things.kind.document',
  sheet: 'things.kind.sheet',
  pdf: 'things.kind.pdf',
  web: 'things.kind.web',
  file: 'things.kind.file'
}

export function kindLabelKey(kind: string): string {
  return KIND_LABEL_KEY[kind] ?? 'things.kind.file'
}

export function thingKindIcon(kind: string): React.JSX.Element {
  const size = 16
  switch (kind) {
    case 'image':
      return <ImageIcon size={size} />
    case 'sheet':
      return <Table size={size} />
    case 'pdf':
      return <File size={size} />
    case 'web':
      return <Globe size={size} />
    case 'document':
      return <FileText size={size} />
    default:
      return <File size={size} />
  }
}

export function formatThingSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function formatThingTime(iso: string | number): string {
  // The backend stores Unix seconds (time.time()) and ships them as JSON
  // numbers; handle seconds AND ms in both string and number form.
  const raw = typeof iso === 'number' ? iso : Number(iso)
  const date = Number.isFinite(raw) && raw > 0
    ? new Date(raw < 1e12 ? raw * 1000 : raw)
    : new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  if (sameDay) {
    return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }
  const thisYear = date.getFullYear() === now.getFullYear()
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(thisYear ? {} : { year: 'numeric' })
  })
}
