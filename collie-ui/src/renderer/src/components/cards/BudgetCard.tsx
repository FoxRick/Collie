interface Props {
  data: Record<string, unknown>
}

export default function BudgetCard({ data }: Props): React.JSX.Element {
  const categories = data.categories as Array<Record<string, unknown>> | undefined
  const totalBudget = (data.total_budget as number) || 0
  const totalSpent = (data.total_spent as number) || 0

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-lg">💰</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Budget
        </span>
        {totalBudget > 0 && (
          <span className="ml-auto text-xs" style={{ color: 'var(--collie-paw)' }}>
            {totalSpent} / {totalBudget}
          </span>
        )}
      </div>
      {categories && categories.length > 0 ? (
        <div className="space-y-2">
          {categories.map((cat, i) => (
            <div key={i}>
              <div className="mb-1 flex justify-between text-xs">
                <span style={{ color: 'var(--collie-nose)' }}>{cat.name as string}</span>
                <span style={{ color: 'var(--collie-paw)' }}>
                  {cat.spent as number} / {cat.budget as number}
                </span>
              </div>
              <div className="h-2 overflow-hidden rounded-full" style={{ background: 'var(--collie-fur)' }}>
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.min(100, ((cat.spent as number) / (cat.budget as number || 1)) * 100)}%`,
                    background: (cat.spent as number) > (cat.budget as number) * 0.8
                      ? 'var(--collie-snoot)'
                      : 'var(--collie-grass)'
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="py-4 text-center text-sm" style={{ color: 'var(--collie-paw)' }}>
          No budget data yet.
        </div>
      )}
    </div>
  )
}
