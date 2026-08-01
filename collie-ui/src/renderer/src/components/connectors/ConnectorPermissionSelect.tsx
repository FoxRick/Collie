export default function ConnectorPermissionSelect({
  value,
  onChange
}: {
  value: string
  onChange: (value: string) => void
}): React.JSX.Element {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">When should Collie ask?</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border bg-white px-3 py-2"
        style={{ borderColor: 'var(--collie-border)' }}
      >
        <option value="every_time">Ask every time</option>
        <option value="changes">Ask for changes</option>
        <option value="important">Ask for important actions (recommended)</option>
      </select>
    </label>
  )
}
