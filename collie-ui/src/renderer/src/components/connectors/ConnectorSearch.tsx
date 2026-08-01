import { Search } from 'lucide-react'

export default function ConnectorSearch({
  value,
  onChange
}: {
  value: string
  onChange: (value: string) => void
}): React.JSX.Element {
  return (
    <label
      className="flex items-center gap-2 rounded-xl border bg-white px-3 py-2.5"
      style={{ borderColor: 'var(--collie-border)' }}
    >
      <Search size={16} style={{ color: 'var(--collie-paw)' }} />
      <span className="sr-only">Search connections</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Search apps"
        className="w-full bg-transparent text-sm outline-none"
      />
    </label>
  )
}
