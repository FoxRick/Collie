// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import TodayGlanceCard from './TodayGlanceCard'

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

const fullData = {
  date: 'Saturday, August 22',
  weather: {
    location: 'Berlin, Germany',
    icon: '☀️',
    temp: '24',
    condition: 'Clear',
    high: '26',
    low: '14',
    rain_chance: 10
  },
  reminders: [
    { text: 'Call the dentist', due_at: '2026-08-22T15:00:00+00:00' },
    { text: 'Take out trash', due_at: '2026-08-23T08:30:00+00:00' }
  ]
}

describe('TodayGlanceCard', () => {
  it('renders weather and reminder sections together', () => {
    const container = render(<TodayGlanceCard data={fullData} />)
    expect(container.querySelector('[aria-label="Today at a glance"]')).not.toBeNull()
    expect(container.textContent).toContain('Today at a glance')
    expect(container.textContent).toContain('24°C')
    expect(container.textContent).toContain('Clear')
    expect(container.textContent).toContain('Call the dentist')
    expect(container.textContent).toContain('Coming up')
  })

  it('renders numeric temperatures from the Open-Meteo payload', () => {
    const container = render(
      <TodayGlanceCard
        data={{
          weather: {
            location: 'Berlin, Germany',
            icon: '☀️',
            temp: 24.3,
            condition: 'Clear',
            high: 26.1,
            low: 14.2,
            rain_chance: 10
          }
        }}
      />
    )

    expect(container.querySelector('[aria-label="Today at a glance"]')).not.toBeNull()
    expect(container.textContent).toContain('24.3°C')
    expect(container.textContent).toContain('H 26.1° · L 14.2°')
  })

  it('shows the rain hint only above the threshold', () => {
    const rainy = {
      ...fullData,
      weather: { ...fullData.weather, rain_chance: 60 }
    }
    const container = render(<TodayGlanceCard data={rainy} />)
    expect(container.textContent).toContain('60% rain')

    const dry = render(<TodayGlanceCard data={{ ...fullData, reminders: [] }} />)
    expect(dry.textContent).not.toContain('% rain')
  })

  it('renders reminders only when there is no weather data', () => {
    const container = render(
      <TodayGlanceCard
        data={{
          reminders: [{ text: 'Water the plants', due_at: '2026-08-22T18:00:00' }]
        }}
      />
    )
    expect(container.querySelector('[aria-label="Today at a glance"]')).not.toBeNull()
    expect(container.textContent).toContain('Water the plants')
    expect(container.textContent).not.toContain('°C')
  })

  it('renders nothing when every section is empty or malformed', () => {
    expect(
      render(<TodayGlanceCard data={{}} />).querySelector('[aria-label="Today at a glance"]')
    ).toBeNull()
    expect(
      render(<TodayGlanceCard data={{ reminders: [{}, { text: '   ' }] }} />).querySelector(
        '[aria-label="Today at a glance"]'
      )
    ).toBeNull()
  })
})
