import { PawPrint } from 'lucide-react'
import type { TakeawayChip, TakeawayDigest, TakeawayPoint } from '../../lib/takeaway'

function MiniBars({ numbers }: { numbers: number[] }): React.JSX.Element {
  const max = Math.max(...numbers, 1)
  return (
    <span
      className="mt-1 flex items-end gap-[3px]"
      aria-hidden="true"
      title={numbers.join(' → ')}
    >
      {numbers.map((value, index) => (
        <span
          key={`${value}-${index}`}
          className="w-1.5 rounded-sm bg-amber-400/80"
          style={{ height: `${Math.max(4, Math.round((value / max) * 14))}px` }}
        />
      ))}
    </span>
  )
}

function PointRow({ point }: { point: TakeawayPoint }): React.JSX.Element {
  return (
    <li className="flex items-start gap-2 text-sm leading-snug text-stone-700">
      <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
      <span className="min-w-0">
        {point.text}
        {point.numbers && point.numbers.length > 0 && <MiniBars numbers={point.numbers} />}
      </span>
    </li>
  )
}

function ChipPill({ chip }: { chip: TakeawayChip }): React.JSX.Element {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">
      <span className="font-medium text-stone-900">{chip.key}</span>
      <span className="text-stone-300" aria-hidden="true">·</span>
      <span>{chip.value}</span>
    </span>
  )
}

/**
 * A friendly closer shown under long answers: a few key points, optional
 * key/value chips and tiny bar sparklines when numbers appear. It always
 * renders *below* the full streamed text — never instead of it.
 */
export default function TakeawayCard({ digest }: { digest: TakeawayDigest }): React.JSX.Element {
  return (
    <section
      aria-label="Quick recap"
      className="my-2 w-full max-w-2xl rounded-xl border border-stone-200 bg-white text-stone-900 shadow-sm"
    >
      <div className="flex items-center gap-3 px-4 pt-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-amber-50 text-amber-700">
          <PawPrint size={16} />
        </span>
        <h2 className="text-sm font-semibold">Quick recap</h2>
      </div>
      <ul className="space-y-2 px-4 py-3">
        {digest.points.map((point, index) => <PointRow point={point} key={index} />)}
      </ul>
      {digest.chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-stone-100 px-4 py-2.5">
          {digest.chips.map((chip, index) => <ChipPill chip={chip} key={`${chip.key}-${index}`} />)}
        </div>
      )}
    </section>
  )
}
