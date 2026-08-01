export default function ConnectorRemoveDialog({
  name,
  onCancel,
  onRemove
}: {
  name: string
  onCancel: () => void
  onRemove: () => void
}): React.JSX.Element {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <section role="alertdialog" aria-modal="true" className="w-full max-w-sm rounded-2xl bg-white p-5">
        <h2 className="font-semibold">Remove {name}?</h2>
        <p className="mt-2 text-sm leading-6" style={{ color: 'var(--collie-paw)' }}>
          Collie will lose access immediately. This does not delete anything in the
          provider account.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button className="rounded-lg border px-4 py-2 text-sm" onClick={onCancel}>
            Keep connection
          </button>
          <button
            className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white"
            onClick={onRemove}
          >
            Remove connection
          </button>
        </div>
      </section>
    </div>
  )
}
