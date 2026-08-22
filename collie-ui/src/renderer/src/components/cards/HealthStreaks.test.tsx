// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import HealthCard from './HealthCard'

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

const baseData = {
  streak_days: 3,
  steps: 7500,
  sleep_hours: 7.5,
  water_cups: 6,
  habits: [
    { key: 'steps', label: 'Steps', icon: '👟', color: '#e8913a', days: [5000, 8000, null, 6200, 9100, 3000, 7500] },
    { key: 'water_cups', label: 'Water', icon: '💧', color: '#6baed6', days: [4, 6, 5, 6, null, 7, 6] }
  ]
}

describe('HealthCard habit streaks', () => {
  it('renders one dot-grid row per logged habit', () => {
    const container = render(<HealthCard data={baseData} />)
    const section = container.querySelector('[aria-label="Habit streaks"]')
    expect(section).not.toBeNull()
    expect(section?.textContent).toContain('Steps')
    expect(section?.textContent).toContain('Water')
    // 7 dots per habit row
    expect(section?.querySelectorAll('[role="img"]').length).toBe(2)
    expect(section?.querySelectorAll('span[aria-hidden="true"].rounded-full').length).toBe(14)
  })

  it('keeps rendering with only the legacy grid and no habits', () => {
    const container = render(<HealthCard data={{ steps: 100, grid: [1, 2, 0, 1, 2, 2, 1] }} />)
    expect(container.textContent).toContain('100')
    // Legacy strip renders inside the (empty) streaks section wrapper
    const strip = container.querySelector('[aria-label="Habit streaks"] > div[aria-hidden="true"]')
    expect(strip?.children.length).toBe(7)
    expect(container.querySelector('[aria-label="Habit streaks"]')?.textContent).not.toContain('Steps')
  })

  it('drops all-empty or malformed habit rows', () => {
    const container = render(
      <HealthCard
        data={{
          habits: [
            { key: 'steps', label: 'Steps', days: [null, null, null, null, null, null, null] },
            { key: 'water', label: 'Water', days: 'nope' },
            null,
            { key: 'sleep', label: 'Sleep', days: [8] }
          ]
        }}
      />
    )
    const labels = container.querySelector('[aria-label="Habit streaks"]')?.textContent ?? ''
    expect(labels).toContain('Sleep')
    expect(labels).not.toContain('Steps')
    expect(labels).not.toContain('Water')
  })
})
