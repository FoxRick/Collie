// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { ApprovalRequest } from '../../lib/ipc'
import ApprovalSheet, { friendlyApprovalLabel } from './ApprovalSheet'

const { resolveApproval, approveAllForRun } = vi.hoisted(() => ({
  resolveApproval: vi.fn().mockResolvedValue({}),
  approveAllForRun: vi.fn().mockResolvedValue({})
}))

vi.mock('../../lib/ipc', () => ({
  collieClient: {
    resolveApproval,
    approveAllForRun
  }
}))

const approval = (id: string, summary: string): ApprovalRequest => ({
  id,
  action: 'file.write',
  resource: `C:\\workspace\\${id}.txt`,
  risk: 'local_write',
  display_json: JSON.stringify({ summary, reversible: true }),
  run_id: null
})

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
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  for (const root of roots.splice(0)) act(() => root.unmount())
  document.body.replaceChildren()
  resolveApproval.mockClear()
  approveAllForRun.mockClear()
})

describe('ApprovalSheet accessibility', () => {
  it('offers run-wide approval only when the backend marks the request eligible', () => {
    const request = {
      ...approval('eligible', 'Update this local file'),
      display_json: JSON.stringify({
        summary: 'Update this local file',
        approve_for_me_eligible: true
      }),
      run_id: 'run-1'
    }
    const container = render(
      <ApprovalSheet approval={request} inline onResolved={() => undefined} />
    )
    expect(container.textContent).toContain('Allow for this run')
    expect(container.textContent).toContain('Approve all ordinary requests for this run')
  })

  it.each([
    ['absent', { summary: 'Update this local file' }],
    ['false', { summary: 'Update this local file', approve_for_me_eligible: false }]
  ])('does not infer run-wide eligibility from local-write risk when the flag is %s', (_case, display) => {
    const request = {
      ...approval('ineligible', 'Update this local file'),
      display_json: JSON.stringify(display),
      run_id: 'run-1'
    }
    const container = render(
      <ApprovalSheet approval={request} inline onResolved={() => undefined} />
    )
    expect(container.textContent).not.toContain('Allow for this run')
    expect(container.textContent).not.toContain('Approve all ordinary requests for this run')
    expect(container.textContent).toContain('Allow once')
  })

  it('renders simultaneous inline approvals as uniquely labelled regions', () => {
    const container = render(
      <>
        <ApprovalSheet approval={approval('first', 'First action')} inline onResolved={() => undefined} />
        <ApprovalSheet approval={approval('second', 'Second action')} inline onResolved={() => undefined} />
      </>
    )

    const regions = Array.from(container.querySelectorAll<HTMLElement>('article[role="region"]'))
    expect(regions).toHaveLength(2)
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    const labels = regions.map((region) => region.getAttribute('aria-labelledby'))
    expect(new Set(labels).size).toBe(2)
    for (const [index, region] of regions.entries()) {
      const label = region.getAttribute('aria-labelledby')
      expect(label).toBeTruthy()
      const heading = region.querySelector<HTMLHeadingElement>('h2')
      expect(heading?.id).toBe(label)
      expect(heading?.textContent).toBe(
        index === 0 ? 'First action' : 'Second action'
      )
    }
  })

  it('uses a plain-language label for technical action identifiers', () => {
    expect(friendlyApprovalLabel('Use web_fetch', 'web_fetch')).toBe('Visit a website')
    expect(friendlyApprovalLabel(undefined, 'custom__task-run')).toBe('Custom Task Run')

    const container = render(
      <ApprovalSheet approval={approval('friendly', 'Use web_fetch')} inline onResolved={() => undefined} />
    )
    expect(container.querySelector('h2')?.textContent).toBe('Visit a website')
    expect(container.textContent).not.toContain('web_fetch')
  })

  it('keeps technical approval details in a collapsed disclosure', () => {
    const container = render(
      <ApprovalSheet approval={approval('details', 'A clear action')} inline onResolved={() => undefined} />
    )
    const details = container.querySelector<HTMLDetailsElement>('details.approval-details')
    expect(details?.open).toBe(false)
    expect(details?.querySelector('summary')?.textContent).toBe('Review details')
    expect(details?.textContent).toContain('Data leaving this computer')
    expect(details?.textContent).toContain('Reversible')
  })

  it('moves focus into a floating dialog and traps forward and reverse Tab', () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Previous focus'
    document.body.append(trigger)
    trigger.focus()
    const container = render(
      <ApprovalSheet approval={approval('focus', 'Focused action')} onResolved={() => undefined} />
    )

    const dialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    const buttons = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button'))
    expect(document.activeElement).toBe(buttons[0])

    buttons.at(-1)!.focus()
    act(() => {
      buttons.at(-1)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    })
    expect(document.activeElement).toBe(buttons[0])

    buttons[0].focus()
    act(() => {
      buttons[0].dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })
      )
    })
    expect(document.activeElement).toBe(buttons.at(-1))
  })

  it('rejects a floating approval on Escape and restores prior focus', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Open approval'
    document.body.append(trigger)
    trigger.focus()

    function Harness(): React.JSX.Element | null {
      const [open, setOpen] = useState(true)
      return open ? (
        <ApprovalSheet
          approval={approval('escape', 'Escape action')}
          onResolved={() => setOpen(false)}
        />
      ) : null
    }

    const container = render(<Harness />)
    const dialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    await act(async () => {
      dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await Promise.resolve()
    })

    expect(resolveApproval).toHaveBeenCalledWith('escape', 'reject', undefined, undefined)
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('does not turn Escape into rejection for inline approvals', () => {
    const container = render(
      <ApprovalSheet approval={approval('inline', 'Inline action')} inline onResolved={() => undefined} />
    )
    const region = container.querySelector<HTMLElement>('[role="region"]')!
    act(() => {
      region.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('gives only the top floating approval modal ownership', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Settings control'
    document.body.append(trigger)
    trigger.focus()
    let removeBackground = (): void => undefined

    function StackHarness(): React.JSX.Element {
      const [items, setItems] = useState([
        approval('bottom', 'Bottom action'),
        approval('middle', 'Middle action'),
        approval('top', 'Top action')
      ])
      const remove = (id: string): void => {
        setItems((current) => current.filter((item) => item.id !== id))
      }
      removeBackground = () => remove('bottom')
      return (
        <>
          {items.map((item, index) => (
            <ApprovalSheet
              key={item.id}
              approval={item}
              activeModal={index === items.length - 1}
              onResolved={() => remove(item.id)}
            />
          ))}
        </>
      )
    }

    const container = render(<StackHarness />)
    expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="region"][hidden]')).toHaveLength(2)
    let activeDialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(activeDialog.textContent).toContain('Top action')
    expect(activeDialog.contains(document.activeElement)).toBe(true)

    act(() => removeBackground())
    expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    expect(container.querySelectorAll('[role="region"][hidden]')).toHaveLength(1)
    activeDialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(activeDialog.textContent).toContain('Top action')
    expect(activeDialog.contains(document.activeElement)).toBe(true)

    await act(async () => {
      activeDialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await Promise.resolve()
    })
    expect(resolveApproval).toHaveBeenCalledWith('top', 'reject', undefined, undefined)
    expect(container.querySelectorAll('[role="dialog"]')).toHaveLength(1)
    const promotedDialog = container.querySelector<HTMLElement>('[role="dialog"]')!
    expect(promotedDialog.textContent).toContain('Middle action')
    expect(promotedDialog.contains(document.activeElement)).toBe(true)
  })
})
