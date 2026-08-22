// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import ReminderCard from './ReminderCard'

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

function iso(daysAhead: number, hour = 12): string {
  const date = new Date()
  date.setDate(date.getDate() + daysAhead)
  date.setHours(hour, 0, 0, 0)
  return date.toISOString()
}

describe('ReminderCard countdown chips', () => {
  it('shows a today! chip and an in-days chip', () => {
    const container = render(
      <ReminderCard
        data={{
          items: [
            { text: 'Call the dentist', due_at: iso(0, 15) },
            { text: 'Water the plants', due_at: iso(3) }
          ]
        }}
      />
    )
    expect(container.textContent).toContain('today!')
    expect(container.textContent).toContain('in 3 days')
    expect(container.textContent).not.toContain('overdue')
  })

  it('hides chips for far-away reminders', () => {
    const container = render(
      <ReminderCard data={{ items: [{ text: 'Renew passport', due_at: iso(30) }] }} />
    )
    expect(container.textContent).toContain('Renew passport')
    expect(container.querySelector('.rounded-full.px-2')).toBeNull()
  })

  it('marks overdue reminders with a loud chip', () => {
    const container = render(
      <ReminderCard data={{ items: [{ text: 'Late thing', due_at: iso(-1, 1) }] }} />
    )
    expect(container.textContent).toContain('overdue')
  })
})
