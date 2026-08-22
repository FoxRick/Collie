// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RoutinesScreen from './RoutinesScreen'
import type { CollieAutomation, CollieRun } from '../lib/ipc'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const hooks = vi.hoisted(() => {
  const client = {
    connected: true,
    on: vi.fn(() => () => undefined),
    listRoutines: vi.fn(async () => ({ routines: [] as CollieAutomation[] })),
    listRoutineRuns: vi.fn(async () => ({ runs: [] as CollieRun[] })),
    pauseRoutine: vi.fn(),
    resumeRoutine: vi.fn(),
    createAutomation: vi.fn(),
    updateAutomation: vi.fn(),
    deleteAutomation: vi.fn()
  }
  return { client }
})

vi.mock('lucide-react', () => ({
  Clock3: () => null,
  History: () => null,
  Pause: () => null,
  Pencil: () => null,
  Play: () => null,
  Plus: () => null,
  RotateCcw: () => null,
  Rocket: () => null,
  Settings2: () => null,
  Trash2: () => null,
  Undo2: () => null,
  X: () => null
}))
vi.mock('../lib/ipc', () => ({ collieClient: hooks.client }))

/** Set a controlled textarea value the way React's onChange actually hears. */
const typeInto = (textarea: HTMLTextAreaElement, value: string): void => {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value'
  )!.set!
  setter.call(textarea, value)
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
}

const routine = (over: Partial<CollieAutomation>): CollieAutomation => ({
  id: 'r1',
  name: 'Morning Briefing',
  description: 'Weather + calendar',
  schedule: '07:00',
  enabled: 1,
  ...over
})

let container: HTMLDivElement | null = null
let root: Root | null = null

const renderScreen = async (): Promise<void> => {
  container = document.body.appendChild(document.createElement('div'))
  root = createRoot(container)
  await act(async () => {
    root?.render(<RoutinesScreen />)
  })
}

describe('RoutinesScreen UX polish', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    root?.unmount()
    container?.remove()
    container = null
    root = null
  })

  it('shows Paused instead of a stale past Next date', async () => {
    hooks.client.listRoutines.mockResolvedValue({
      routines: [
        routine({
          id: 'paused-one',
          name: 'Evening Wind-Down',
          enabled: 0,
          next_run_at: '2026-08-02T21:00:00+00:00'
        })
      ]
    })
    await renderScreen()
    expect(document.querySelector('main')!.textContent).not.toContain('8/2/2026')
    expect(document.querySelector('main')!.textContent).toContain('Next: Paused')
  })

  it('hides the Plan badge for routines without a plan version', async () => {
    hooks.client.listRoutines.mockResolvedValue({ routines: [routine({ plan_version: null as unknown as number })] })
    await renderScreen()
    expect(document.querySelector('main')!.textContent).not.toContain('Plan:')
  })

  it('keeps the Plan badge when the routine has a plan version', async () => {
    hooks.client.listRoutines.mockResolvedValue({ routines: [routine({ plan_version: 3 })] })
    await renderScreen()
    expect(document.querySelector('main')!.textContent).toContain('Plan: v3')
  })

  it('renders human schedule text instead of raw codes', async () => {
    hooks.client.listRoutines.mockResolvedValue({
      routines: [routine({ schedule: 'Sun 18:00' })]
    })
    await renderScreen()
    expect(document.querySelector('main')!.textContent).toContain('Sundays at 6 pm')
  })

  it('groups system-maintenance routines under their own label', async () => {
    hooks.client.listRoutines.mockResolvedValue({
      routines: [
        routine({ id: 'user-one', name: 'My Custom Routine' }),
        routine({ id: 'collie-memory-maintenance', name: 'Memory maintenance' }),
        routine({ id: 'collie-gardener-suggestions', name: 'Improvement suggestions' })
      ]
    })
    await renderScreen()
    const mainText = document.querySelector('main')!
    const groupLabel = Array.from(mainText.querySelectorAll('h3')).find((h) =>
      h.textContent!.includes('System maintenance')
    )
    expect(groupLabel).toBeTruthy()
    // The user's routine must appear before the system group in DOM order.
    const userIndex = mainText.textContent!.indexOf('My Custom Routine')
    const systemIndex = mainText.textContent!.indexOf('Memory maintenance')
    expect(userIndex).toBeGreaterThanOrEqual(0)
    expect(systemIndex).toBeGreaterThan(userIndex)
  })

  it('labels missed-window skips as Missed with reassurance, not error jargon', async () => {
    hooks.client.listRoutines.mockResolvedValue({ routines: [routine({})] })
    hooks.client.listRoutineRuns.mockResolvedValue({
      runs: [
        {
          id: 'run-1',
          status: 'skipped',
          trigger_type: 'schedule',
          scheduled_for: '2026-08-20T07:00:00+00:00',
          error_code: 'missed_window'
        } as unknown as CollieRun
      ]
    })
    await renderScreen()
    await act(async () => {
      ;(
        Array.from(document.querySelectorAll('button')).find((b) =>
          b.textContent!.includes('History')
        ) as HTMLButtonElement
      ).click()
    })
    const mainText = document.querySelector('main')!.textContent!
    expect(mainText).toContain('Missed')
    expect(mainText).toContain('Your computer was likely off')
    expect(mainText).not.toContain('missed-run window')
  })

  it('uses the friendlier subtitle', async () => {
    hooks.client.listRoutines.mockResolvedValue({ routines: [] })
    await renderScreen()
    expect(document.querySelector('.section-header p')!.textContent).toBe(
      'Put tasks on repeat — see what ran and fix misses.'
    )
  })

  it('shows a confirmation banner with Undo after creating a routine', async () => {
    hooks.client.listRoutines
      .mockResolvedValueOnce({ routines: [] })
      .mockResolvedValue({
        routines: [
          routine({
            id: 'custom-new',
            name: 'Every weekday at 8am, brief me',
            action_type: 'custom'
          })
        ]
      })
    hooks.client.createAutomation.mockResolvedValue({
      automation: { id: 'custom-new', name: 'Every weekday at 8am, brief me' }
    })
    await renderScreen()
    // Create one via the dialog.
    await act(async () => {
      ;(
        Array.from(document.querySelectorAll('button')).find((b) =>
          b.textContent!.includes('Create routine')
        ) as HTMLButtonElement
      ).click()
    })
    const textarea = document.querySelector('.dialog-card textarea') as HTMLTextAreaElement
    await act(async () => {
      typeInto(textarea, 'Every weekday at 8am, brief me on the day')
    })
    await act(async () => {
      ;(
        Array.from(document.querySelectorAll('.dialog-card button')).find(
          (b) => b.textContent!.trim() === 'Create routine'
        ) as HTMLButtonElement
      ).click()
    })
    const banner = document.querySelector('.routine-created-banner')
    expect(banner).toBeTruthy()
    expect(banner!.textContent).toContain('Collie will repeat')
    // One-tap Undo removes it.
    hooks.client.deleteAutomation.mockResolvedValue({ deleted: true })
    await act(async () => {
      ;(banner!.querySelector('button') as HTMLButtonElement).click()
    })
    expect(hooks.client.deleteAutomation).toHaveBeenCalledWith('custom-new')
    expect(document.querySelector('.routine-created-banner')).toBeNull()
  })

  it('edits a custom routine through the pencil button and dialog', async () => {
    hooks.client.listRoutines.mockResolvedValue({
      routines: [routine({ id: 'custom-1', description: 'jog every Tuesday at 7am', action_type: 'custom' })]
    })
    hooks.client.updateAutomation.mockResolvedValue({
      automation: { id: 'custom-1', description: 'jog every Friday at 7am' }
    })
    await renderScreen()
    await act(async () => {
      ;(
        document.querySelector('.loop-edit[aria-label="Edit Morning Briefing"]') as HTMLButtonElement
      ).click()
    })
    const dialog = document.querySelector('.dialog-card')
    expect(dialog).toBeTruthy()
    expect(dialog!.textContent).toContain('Change what Collie repeats')
    const textarea = dialog!.querySelector('textarea') as HTMLTextAreaElement
    expect(textarea.value).toBe('jog every Tuesday at 7am')
    await act(async () => {
      typeInto(textarea, 'jog every Friday at 7am')
    })
    await act(async () => {
      ;(
        Array.from(dialog!.querySelectorAll('button')).find(
          (b) => b.textContent!.trim() === 'Save changes'
        ) as HTMLButtonElement
      ).click()
    })
    expect(hooks.client.updateAutomation).toHaveBeenCalledWith(
      'custom-1',
      'jog every Friday at 7am',
      undefined,
      expect.any(String)
    )
    expect(document.querySelector('.dialog-card')).toBeNull()
  })

  it('does not offer Edit on built-in routines', async () => {
    hooks.client.listRoutines.mockResolvedValue({
      routines: [routine({ id: 'collie-morning-briefing' })]
    })
    await renderScreen()
    expect(document.querySelector('.loop-edit')).toBeNull()
    expect(document.querySelector('.loop-delete')).toBeNull()
  })
})
