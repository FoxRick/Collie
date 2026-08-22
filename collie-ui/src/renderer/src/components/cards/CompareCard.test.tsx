// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import CompareCard, { parseCompareCardData } from './CompareCard'

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
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

const duel = {
  question: 'Which laptop?',
  options: [
    { name: 'Lenovo IdeaPad', price: '$549', wins: ['Lighter', 'Longer battery'] },
    { name: 'Acer Swift', price: '$479', wins: ['Cheaper'] }
  ],
  verdict: 'The Lenovo — the lighter body and longer battery are worth the $70.'
}

describe('parseCompareCardData', () => {
  it('accepts exactly two well-formed options', () => {
    const parsed = parseCompareCardData(duel)
    expect(parsed).not.toBeNull()
    expect(parsed?.options[0].name).toBe('Lenovo IdeaPad')
    expect(parsed?.options[1].price).toBe('$479')
    expect(parsed?.verdict).toContain('Lenovo')
  })

  it('rejects three or more options — that is a table, not a duel', () => {
    const parsed = parseCompareCardData({
      options: [
        { name: 'A', wins: [] },
        { name: 'B', wins: [] },
        { name: 'C', wins: [] }
      ]
    })
    expect(parsed).toBeNull()
  })

  it('rejects one option and malformed records', () => {
    expect(parseCompareCardData({ options: [{ name: 'Only one' }] })).toBeNull()
    expect(parseCompareCardData({ options: [null, { name: '' }, 42] })).toBeNull()
    expect(parseCompareCardData({})).toBeNull()
  })

  it('caps long names, verdicts and win lists defensively', () => {
    const parsed = parseCompareCardData({
      options: [
        { name: 'x'.repeat(200), wins: Array.from({ length: 10 }, (_, i) => `point ${i}`) },
        { name: 'B' }
      ],
      verdict: 'y'.repeat(500)
    })
    expect(parsed?.options[0].name.length).toBeLessThanOrEqual(80)
    expect(parsed?.options[0].wins.length).toBeLessThanOrEqual(3)
    expect(parsed?.verdict?.length).toBeLessThanOrEqual(200)
  })
})

describe('CompareCard', () => {
  it('renders both finalists with names, prices and wins', () => {
    const container = render(<CompareCard data={duel} />)
    expect(container.querySelector('[aria-label="Comparison"]')).not.toBeNull()
    expect(container.textContent).toContain('Which laptop?')
    expect(container.textContent).toContain('Lenovo IdeaPad')
    expect(container.textContent).toContain('$479')
    expect(container.textContent).toContain('Longer battery')
  })

  it("shows Collie's pick verdict", () => {
    const container = render(<CompareCard data={duel} />)
    expect(container.textContent).toContain("Collie's pick")
    expect(container.textContent).toContain('worth the $70')
  })

  it('renders nothing for three or more options', () => {
    const container = render(
      <CompareCard
        data={{
          options: [{ name: 'A' }, { name: 'B' }, { name: 'C' }],
          verdict: 'nope'
        }}
      />
    )
    expect(container.querySelector('[aria-label="Comparison"]')).toBeNull()
  })

  it('works without optional fields', () => {
    const container = render(
      <CompareCard data={{ options: [{ name: 'Tea' }, { name: 'Coffee' }] }} />
    )
    expect(container.textContent).toContain('Which one?')
    expect(container.textContent).toContain('Coffee')
  })
})
