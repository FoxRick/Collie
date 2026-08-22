import { Trophy } from 'lucide-react'

interface CompareOption {
  name: string
  price?: string
  wins: string[]
  icon?: string
}

interface ParsedCompare {
  question?: string
  options: [CompareOption, CompareOption]
  verdict?: string
}

const MAX_NAME_LEN = 80
const MAX_POINT_LEN = 60
const MAX_POINTS_PER_OPTION = 3

function cleanText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function parsePoints(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const points: string[] = []
  for (const entry of value) {
    const text = cleanText(entry)
    if (!text || text.length > MAX_POINT_LEN) continue
    if (points.length >= MAX_POINTS_PER_OPTION) break
    points.push(text)
  }
  return points.slice(0, MAX_POINTS_PER_OPTION)
}

/**
 * Defensive parsing for model/tool-supplied compare data. Only exactly two
 * usable options render a card — three or more is a table job (plain markdown),
 * not a decision card.
 */
export function parseCompareCardData(data: Record<string, unknown>): ParsedCompare | null {
  const rawOptions = Array.isArray(data.options) ? data.options : []
  const parsed: CompareOption[] = []

  for (const raw of rawOptions) {
    if (raw === null || typeof raw !== 'object') continue
    const record = raw as Record<string, unknown>
    const name = cleanText(record.name).slice(0, MAX_NAME_LEN)
    if (!name) continue
    const price = cleanText(record.price).slice(0, 24)
    parsed.push({
      name,
      ...(price ? { price } : {}),
      wins: parsePoints(record.wins ?? record.points),
      ...(cleanText(record.icon) ? { icon: cleanText(record.icon).slice(0, 8) } : {})
    })
  }

  // Exactly two — never truncate a bigger list into a fake duel.
  if (parsed.length !== 2) return null

  const question = cleanText(data.question).slice(0, 120)
  const verdict = cleanText(data.verdict ?? data.why).slice(0, 200)

  return {
    ...(question ? { question } : {}),
    options: [parsed[0], parsed[1]],
    ...(verdict ? { verdict } : {})
  }
}

function OptionColumn({ option, winner }: { option: CompareOption; winner: boolean }): React.JSX.Element {
  return (
    <div
      className="flex-1 rounded-xl p-3"
      style={{
        background: winner ? 'var(--collie-bone)' : 'transparent',
        border: `1px solid ${winner ? 'var(--collie-grass)' : 'var(--collie-fur)'}`
      }}
    >
      <div className="flex items-center gap-2">
        {option.icon && <span className="text-lg" aria-hidden="true">{option.icon}</span>}
        <span className="min-w-0 flex-1 truncate text-sm font-semibold" style={{ color: 'var(--collie-nose)' }}>
          {option.name}
        </span>
      </div>
      {option.price && (
        <div className="mt-0.5 text-sm font-bold" style={{ color: 'var(--collie-amber)' }}>
          {option.price}
        </div>
      )}
      {option.wins.length > 0 && (
        <ul className="mt-2 space-y-1">
          {option.wins.map((point, index) => (
            <li key={index} className="flex items-start gap-1.5 text-xs leading-snug" style={{ color: 'var(--collie-paw)' }}>
              <span aria-hidden="true" style={{ color: 'var(--collie-grass)' }}>✓</span>
              <span className="min-w-0">{point}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * A vs B duel card for "which should I pick?" answers. Shows exactly two
 * side-by-side finalists with a clear Collie's pick — never a wide grid.
 * Data with more than two options (or fewer) renders nothing; the streamed
 * text above stays the full answer.
 */
export default function CompareCard({ data }: { data: Record<string, unknown> }): React.JSX.Element | null {
  const parsed = parseCompareCardData(data)
  if (!parsed) return null

  return (
    <section
      aria-label="Comparison"
      className="collie-reveal mt-3 w-full max-w-md rounded-2xl border p-4"
      style={{ background: 'var(--collie-bg)', borderColor: 'var(--collie-fur)' }}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="text-lg" aria-hidden="true">⚖️</span>
        <span className="font-semibold" style={{ color: 'var(--collie-nose)' }}>
          {parsed.question || 'Which one?'}
        </span>
      </div>

      <div className="flex items-stretch gap-2">
        <div className="w-4 shrink-0" aria-hidden="true" />
        <OptionColumn option={parsed.options[0]} winner={false} />
        <OptionColumn option={parsed.options[1]} winner={false} />
      </div>

      {parsed.verdict && (
        <div className="mt-3 flex items-start gap-2 rounded-xl p-3" style={{ background: 'var(--collie-bone)' }}>
          <Trophy size={16} className="mt-0.5 shrink-0" style={{ color: 'var(--collie-amber)' }} aria-hidden="true" />
          <p className="text-xs leading-snug" style={{ color: 'var(--collie-nose)' }}>
            <span className="font-semibold">Collie's pick: </span>
            {parsed.verdict}
          </p>
        </div>
      )}
    </section>
  )
}
