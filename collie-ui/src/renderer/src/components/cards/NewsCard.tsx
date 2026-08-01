interface Props {
  data: Record<string, unknown>
}

export default function NewsCard({ data }: Props): React.JSX.Element {
  const articles = data.articles as Array<Record<string, unknown>> | undefined

  if (!articles || articles.length === 0) {
    return (
      <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
        style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
        <div className="flex items-center gap-2">
          <span className="text-lg">📰</span>
          <span className="text-sm" style={{ color: 'var(--collie-paw)' }}>
            No news right now.
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg">📰</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          News
        </span>
      </div>
      <div className="space-y-2">
        {articles.map((article, i) => (
          <div key={i} className="flex gap-3 rounded-xl p-2" style={{ background: 'var(--collie-bone)' }}>
            {(article.image as string) && (
              <div className="h-14 w-14 shrink-0 overflow-hidden rounded-lg">
                <img
                  src={String(article.image || '')}
                  alt=""
                  className="h-full w-full object-cover"
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="line-clamp-2 text-sm font-semibold" style={{ color: 'var(--collie-nose)' }}>
                {article.headline as string}
              </div>
              <div className="mt-1 text-xs" style={{ color: 'var(--collie-paw)' }}>
                {article.source as string}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
