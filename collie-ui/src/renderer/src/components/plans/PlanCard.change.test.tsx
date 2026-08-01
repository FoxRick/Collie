// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import PlanCard from './PlanCard'
import {
  PLAN_CHANGE_REQUEST_EVENT,
  publishPlanChangeResult,
  type PlanChangeRequest
} from './planChange'

const roots: Root[] = []

function render(element: React.ReactNode): HTMLElement {
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  roots.push(root)
  act(() => root.render(element))
  return container
}

const data = {
  plan_id: 'plan-1', version: 2, plan_hash: 'hash',
  plan: { title: 'Move house', goal: 'Plan the move', steps: [] }
}

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
})

describe('PlanCard safe Change plan', () => {
  it('shows a truthful pending-safe-boundary state and disables duplicate requests', () => {
    let calls = 0
    const onRequest = (event: Event): void => {
      calls += 1
      const detail = (event as CustomEvent<PlanChangeRequest>).detail
      publishPlanChangeResult({
        ...detail,
        state: 'pending_safe_boundary',
        message: 'Collie is finishing its current safe step, then the plan will pause.'
      })
    }
    window.addEventListener(PLAN_CHANGE_REQUEST_EVENT, onRequest)
    const container = render(<PlanCard data={data} />)
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('Change plan'))
    act(() => {
      button?.click()
      button?.click()
    })
    expect(calls).toBe(1)
    expect(button?.disabled).toBe(true)
    expect(button?.textContent).toContain('Waiting for a safe pause')
    const status = container.querySelector('[role="status"]')
    expect(status?.getAttribute('aria-live')).toBe('polite')
    expect(status?.textContent).not.toContain('The plan is paused')
    window.removeEventListener(PLAN_CHANGE_REQUEST_EVENT, onRequest)
  })

  it('ignores a stale result for another plan and permits a retry after an error', () => {
    const onRequest = (event: Event): void => {
      const detail = (event as CustomEvent<PlanChangeRequest>).detail
      publishPlanChangeResult({
        planId: 'other-plan', version: detail.version, state: 'paused', message: 'Wrong plan.'
      })
      publishPlanChangeResult({
        ...detail, state: 'error', message: 'Could not reach a safe boundary. Try again.'
      })
    }
    window.addEventListener(PLAN_CHANGE_REQUEST_EVENT, onRequest)
    const container = render(<PlanCard data={data} />)
    const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('Change plan'))
    act(() => button?.click())
    expect(button?.disabled).toBe(false)
    expect(button?.textContent).toContain('Try again')
    expect(container.textContent).not.toContain('Wrong plan.')
    window.removeEventListener(PLAN_CHANGE_REQUEST_EVENT, onRequest)
  })
})
