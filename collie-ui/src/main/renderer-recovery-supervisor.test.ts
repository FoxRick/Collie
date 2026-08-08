import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  RendererRecoverySupervisor,
  type RendererRecoverySupervisorDeps
} from './renderer-recovery'

function makeSupervisor(overrides: Partial<RendererRecoverySupervisorDeps> = {}) {
  const reloadWindow = vi.fn()
  const showDialog = vi.fn()
  const deps: RendererRecoverySupervisorDeps = {
    reloadWindow,
    showDialog,
    now: () => Date.now(),
    scheduleReload: (fn, delayMs) => setTimeout(fn, delayMs),
    cancelReload: (handle) => clearTimeout(handle as NodeJS.Timeout),
    ...overrides
  }
  const supervisor = new RendererRecoverySupervisor(deps)
  return { supervisor, deps, reloadWindow, showDialog }
}

beforeEach(() => {
  vi.useFakeTimers({ now: 0 })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RendererRecoverySupervisor', () => {
  it('schedules a reload when the renderer crashes', () => {
    const { supervisor, reloadWindow } = makeSupervisor()
    expect(supervisor.renderProcessGone('oom')).toBe(true)
    expect(reloadWindow).not.toHaveBeenCalled()
    vi.advanceTimersByTime(250)
    expect(reloadWindow).toHaveBeenCalledTimes(1)
  })

  it('applies the backoff delay before reloading', () => {
    const { supervisor, reloadWindow } = makeSupervisor()
    supervisor.renderProcessGone('crashed')
    vi.advanceTimersByTime(249)
    expect(reloadWindow).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(reloadWindow).toHaveBeenCalledTimes(1)
  })

  it('cancels the pending reload on quit during the backoff window', () => {
    // index.ts calls cancelPendingReload() from before-quit: quitting while a
    // reload is scheduled must NOT still fire the reload.
    const { supervisor, reloadWindow } = makeSupervisor()
    supervisor.renderProcessGone('crashed')
    supervisor.cancelPendingReload()
    vi.advanceTimersByTime(10_000)
    expect(reloadWindow).not.toHaveBeenCalled()
  })

  it('cancels the pending reload when the window is replaced', () => {
    // index.ts calls cancelPendingReload() at the top of createWindow() so a
    // stale timer can never reload the WRONG (replacement) window. After the
    // cancel, a fresh crash schedules a fresh reload and only that one fires.
    const { supervisor, reloadWindow } = makeSupervisor()
    supervisor.renderProcessGone('crashed') // delay 250ms
    supervisor.cancelPendingReload() // window replaced: stale timer dropped
    supervisor.renderProcessGone('crashed') // fresh crash: delay 500ms
    expect(reloadWindow).not.toHaveBeenCalled()
    vi.advanceTimersByTime(250) // the stale 250ms timer would have fired here
    expect(reloadWindow).not.toHaveBeenCalled()
    vi.advanceTimersByTime(250) // now the 500ms timer fires
    expect(reloadWindow).toHaveBeenCalledTimes(1)
  })

  it('does not double-schedule on duplicate render-process-gone events', () => {
    const { supervisor, reloadWindow } = makeSupervisor()
    supervisor.renderProcessGone('oom')
    expect(vi.getTimerCount()).toBe(1)
    supervisor.renderProcessGone('oom') // duplicate while one is pending
    expect(vi.getTimerCount()).toBe(1) // still exactly one pending timer
    vi.advanceTimersByTime(10_000)
    expect(reloadWindow).toHaveBeenCalledTimes(1)
  })

  it('shows the recovery dialog exactly once across repeated exhaustion', () => {
    const { supervisor, showDialog } = makeSupervisor()
    supervisor.renderProcessGone('oom') // 1st: reload scheduled
    supervisor.renderProcessGone('oom') // 2nd: reload scheduled
    supervisor.renderProcessGone('oom') // 3rd: reload scheduled
    supervisor.renderProcessGone('oom') // 4th: budget exhausted → dialog
    expect(showDialog).toHaveBeenCalledTimes(1)
    supervisor.renderProcessGone('oom') // 5th+: still exhausted → log only
    supervisor.renderProcessGone('oom')
    expect(showDialog).toHaveBeenCalledTimes(1)
    expect(supervisor.renderProcessGone('oom')).toBe(false)
  })

  it('does nothing for launch-failed', () => {
    // A failed launch is not recoverable: reloading the same window state
    // would fail again, so no reload is scheduled and no budget is spent.
    const { supervisor, reloadWindow, showDialog } = makeSupervisor()
    expect(supervisor.renderProcessGone('launch-failed')).toBe(false)
    expect(reloadWindow).not.toHaveBeenCalled()
    expect(showDialog).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does nothing for unrecoverable reasons', () => {
    const { supervisor, reloadWindow } = makeSupervisor()
    expect(supervisor.renderProcessGone('clean-exit')).toBe(false)
    vi.advanceTimersByTime(10_000)
    expect(reloadWindow).not.toHaveBeenCalled()
  })
})
