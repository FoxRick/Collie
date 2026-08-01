interface Props {
  data: Record<string, unknown>
}

export default function HealthCard({ data }: Props): React.JSX.Element {
  const streak = (data.streak_days as number) || 0
  const steps = (data.steps as number) || 0
  const sleep = (data.sleep_hours as number) || 0
  const water = (data.water_cups as number) || 0
  const grid = data.grid as Array<number> | undefined

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-lg">💪</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          Health
        </span>
        {streak > 0 && (
          <span className="ml-auto rounded-full px-2 py-0.5 text-xs font-bold"
            style={{ color: 'white', background: 'var(--collie-grass)' }}>
            {streak} day streak!
          </span>
        )}
      </div>
      <div className="mb-3 grid grid-cols-3 gap-2">
        <div className="rounded-xl p-2 text-center" style={{ background: 'var(--collie-bone)' }}>
          <div className="text-lg font-bold" style={{ color: 'var(--collie-nose)' }}>
            {steps.toLocaleString()}
          </div>
          <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>steps</div>
        </div>
        <div className="rounded-xl p-2 text-center" style={{ background: 'var(--collie-bone)' }}>
          <div className="text-lg font-bold" style={{ color: 'var(--collie-sky)' }}>
            {sleep}h
          </div>
          <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>sleep</div>
        </div>
        <div className="rounded-xl p-2 text-center" style={{ background: 'var(--collie-bone)' }}>
          <div className="text-lg font-bold" style={{ color: 'var(--collie-amber)' }}>
            {water}
          </div>
          <div className="text-xs" style={{ color: 'var(--collie-paw)' }}>cups</div>
        </div>
      </div>
      {grid && grid.length > 0 && (
        <div className="flex gap-1">
          {grid.map((val, i) => (
            <div key={i}
              className="h-3 flex-1 rounded-sm"
              style={{
                background: val > 0
                  ? `color-mix(in srgb, var(--collie-grass) ${val * 25}%, var(--collie-fur))`
                  : 'var(--collie-fur)'
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
