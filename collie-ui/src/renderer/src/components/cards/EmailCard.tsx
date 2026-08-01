interface Props {
  data: Record<string, unknown>
}

export default function EmailCard({ data }: Props): React.JSX.Element {
  const emails = data.emails as Array<Record<string, unknown>> | undefined

  if (!emails || emails.length === 0) {
    return <div />
  }

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">✉️</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Inbox
        </span>
      </div>
      <div className="space-y-2">
        {emails.map((email, i) => (
          <div key={i} className="rounded-xl p-3"
            style={{ background: 'var(--collie-bone)' }}>
            <div className="flex items-start justify-between">
              <div className="font-semibold text-sm" style={{ color: 'var(--collie-nose)' }}>
                {email.sender as string}
              </div>
              <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>
                {email.date as string}
              </div>
            </div>
            <div className="text-sm font-medium" style={{ color: 'var(--collie-nose)' }}>
              {email.subject as string}
            </div>
            <div className="mt-1 line-clamp-2 text-xs" style={{ color: 'var(--collie-paw)' }}>
              {email.preview as string}
            </div>
            <div className="mt-2 flex gap-2">
              <button className="rounded-lg px-3 py-1 text-xs font-medium"
                style={{ color: 'white', background: 'var(--collie-amber)' }}>
                Reply
              </button>
              <button className="rounded-lg px-3 py-1 text-xs font-medium"
                style={{ color: 'var(--collie-paw)', background: 'var(--collie-fur)' }}>
                Archive
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
