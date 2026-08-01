import { CheckCircle2, LoaderCircle } from 'lucide-react'

export default function ConnectorAuthProgress({
  providerName,
  phase,
  onCancel
}: {
  providerName: string
  phase: 'authorizing' | 'testing' | 'connected'
  onCancel?: () => void
}): React.JSX.Element {
  const steps = ['authorizing', 'testing', 'connected'] as const
  const current = steps.indexOf(phase)
  return (
    <div
      role="status"
      className="mb-5 rounded-2xl border bg-white p-4"
      style={{ borderColor: 'var(--collie-border)' }}
    >
      <b className="text-sm">Connecting {providerName}</b>
      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        {steps.map((step, index) => (
          <span
            key={step}
            className="flex items-center gap-1.5"
            style={{ color: index <= current ? 'var(--collie-nose)' : 'var(--collie-paw)' }}
          >
            {index < current || phase === 'connected' ? (
              <CheckCircle2 size={14} />
            ) : index === current ? (
              <LoaderCircle size={14} className="animate-spin" />
            ) : (
              <span className="h-3.5 w-3.5 rounded-full border" />
            )}
            {step === 'authorizing'
              ? 'Authorizing'
              : step === 'testing'
                ? 'Checking connection'
                : 'Connected'}
          </span>
        ))}
      </div>
      {phase !== 'connected' && onCancel ? (
        <button className="mt-3 text-xs underline" onClick={onCancel}>
          Cancel connection
        </button>
      ) : null}
    </div>
  )
}
