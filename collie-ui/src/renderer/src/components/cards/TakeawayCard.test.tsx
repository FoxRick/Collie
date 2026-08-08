// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { buildTakeawayDigest } from '../../lib/takeaway'
import type { CollieMessage } from '../../lib/ipc'
import TakeawayCard from './TakeawayCard'
import MessageList from '../MessageList'

const roots: Root[] = []

function render(element: React.ReactNode): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(element))
  return container
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

const LONG_ANSWER = `Here is a detailed walkthrough of the plan for the garden project.

## Steps

- Clear the flower beds and remove the old trellis
- Plant the tomatoes and the basil in the sunny corner
- Lay down mulch across all three beds before the weekend
- Set up the drip irrigation system with a timer

## Timing

**Start**: Saturday morning
**Budget**: $120 total for soil and seeds

Some extra detail: the soil in the north bed tested quite acidic this year, so
we will mix in a little lime when we turn the beds over, and the basil should
go in last since it grows fastest in the warm weather.`

function assistantMessage(content: string): CollieMessage {
  return {
    id: 'm1',
    conversation_id: 'c1',
    role: 'assistant',
    content,
    created_at: '2026-01-01T00:00:00Z'
  }
}

describe('buildTakeawayDigest', () => {
  it('turns headings and bullets into key points and key-value lines into chips', () => {
    const digest = buildTakeawayDigest(LONG_ANSWER)

    expect(digest).not.toBeNull()
    expect(digest!.points.map((point) => point.text)).toEqual([
      'Steps',
      'Clear the flower beds and remove the old trellis',
      'Plant the tomatoes and the basil in the sunny corner',
      'Lay down mulch across all three beds before the weekend',
      'Set up the drip irrigation system with a timer'
    ])
    expect(digest!.chips).toEqual([
      { key: 'Start', value: 'Saturday morning' },
      { key: 'Budget', value: '$120 total for soil and seeds' }
    ])
  })

  it('returns null for short answers', () => {
    expect(buildTakeawayDigest('Sure — here you go.')).toBeNull()
  })

  it('returns null for long prose without any structure', () => {
    const prose = `This is a long conversational answer that simply keeps going
    and going without using any headings, bullet lists or bold labels at all.
    It talks at length about the weather, about the neighborhood, about the
    plants on the balcony, about the coffee that was left to cool, and about
    how the afternoon light falls across the kitchen floor. There is nothing
    to extract here, no structure to hang a recap on, just words that flow on
    and on like a river that never quite reaches the sea.`
    expect(buildTakeawayDigest(prose)).toBeNull()
  })

  it('attaches a sparkline series when a point contains several numbers', () => {
    const digest = buildTakeawayDigest(`${'Intro words. '.repeat(40)}

- Track the scores across rounds: 12, 18, 23 and 30 points
- Water the seedlings twice a day
- Feed the plants weekly`)

    expect(digest).not.toBeNull()
    const scored = digest!.points.find((point) => point.numbers)
    expect(scored?.numbers).toEqual([12, 18, 23, 30])
  })

  it('ignores fenced code blocks', () => {
    const digest = buildTakeawayDigest(`${'Intro words. '.repeat(40)}

## Steps

- Install the new shelf

\`\`\`
- this is code, not a bullet
- and so is this
\`\`\`

- Paint the wall`)

    expect(digest).not.toBeNull()
    expect(digest!.points.some((point) => point.text.includes('this is code'))).toBe(false)
    expect(digest!.points.some((point) => point.text.includes('Paint the wall'))).toBe(true)
  })
})

describe('TakeawayCard', () => {
  it('renders the recap with points, chips and a sparkline for numbers', () => {
    const digest = buildTakeawayDigest(LONG_ANSWER)!
    const container = render(<TakeawayCard digest={digest} />)

    expect(container.querySelector('[aria-label="Quick recap"]')).not.toBeNull()
    expect(container.textContent).toContain('Clear the flower beds and remove the old trellis')
    expect(container.textContent).toContain('Saturday morning')
    expect(container.textContent).toContain('$120 total for soil and seeds')
  })

  it('renders tiny bars when a point carries a numeric series', () => {
    const digest = buildTakeawayDigest(`${'Intro words. '.repeat(40)}

- Scores across the season: 12, 18, 23 and 30 points
- Keep the courts dry
- Book the courts for Saturday`)!
    const container = render(<TakeawayCard digest={digest} />)

    expect(container.querySelector('[title="12 → 18 → 23 → 30"]')).not.toBeNull()
  })
})

describe('MessageList takeaway integration', () => {
  it('shows the recap under a committed long answer while keeping the full text', () => {
    const container = render(<MessageList messages={[assistantMessage(LONG_ANSWER)]} streamText="" />)

    expect(container.querySelector('[aria-label="Quick recap"]')).not.toBeNull()
    expect(container.textContent).toContain('Clear the flower beds and remove the old trellis')
    expect(container.textContent).toContain('we will mix in a little lime when we turn the beds over')
  })

  it('hides the recap while the answer is still streaming', () => {
    const container = render(<MessageList messages={[]} streamText={LONG_ANSWER} />)

    expect(container.querySelector('[aria-label="Quick recap"]')).toBeNull()
    expect(container.textContent).toContain('Clear the flower beds')
  })

  it('shows no recap for short committed answers', () => {
    const container = render(<MessageList messages={[assistantMessage('All set — done!')]} streamText="" />)

    expect(container.querySelector('[aria-label="Quick recap"]')).toBeNull()
  })
})
