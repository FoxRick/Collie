interface Props {
  data: Record<string, unknown>
}

export default function RecipeCard({ data }: Props): React.JSX.Element {
  const title = (data.title as string) || 'Recipe'
  const prepTime = (data.prep_time as string) || ''
  const cookTime = (data.cook_time as string) || ''
  const servings = (data.servings as string) || ''
  const ingredients = data.ingredients as Array<Record<string, unknown>> | undefined
  const steps = data.steps as Array<string> | undefined
  const heroLine = (data.hero_line as string) || ''

  return (
    <div className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-lg">🍳</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          {title}
        </span>
      </div>
      {heroLine && (
        <div className="mb-2 text-xs italic" style={{ color: 'var(--collie-paw)' }}>
          {heroLine}
        </div>
      )}
      <div className="mb-3 flex gap-3 text-xs" style={{ color: 'var(--collie-paw)' }}>
        {prepTime && <span>Prep: {prepTime}</span>}
        {cookTime && <span>Cook: {cookTime}</span>}
        {servings && <span>{servings} servings</span>}
      </div>
      {ingredients && ingredients.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-semibold" style={{ color: 'var(--collie-amber)' }}>
            Ingredients
          </div>
          <div className="space-y-1">
            {ingredients.map((item, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="accent-[var(--collie-amber)]" />
                <span style={{ color: 'var(--collie-nose)' }}>
                  {item.amount ? `${item.amount as string} ` : ''}{item.name as string}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      {steps && steps.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold" style={{ color: 'var(--collie-amber)' }}>
            Steps
          </div>
          <ol className="space-y-1 text-sm" style={{ color: 'var(--collie-nose)' }}>
            {steps.map((step, i) => (
              <li key={i} className="flex gap-2">
                <span style={{ color: 'var(--collie-amber)' }}>{i + 1}.</span>
                {step}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}
