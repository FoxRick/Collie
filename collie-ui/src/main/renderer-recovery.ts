/**
 * Renderer crash recovery policy: bounded auto-reload after the renderer
 * process is gone (crashed / OOM / killed / abnormal exit).
 *
 * The renderer is disposable — the Python core is a separate process, so a
 * reload fully rehydrates the UI from it (the white-screen-after-OOM failure
 * becomes a ~1s blip). We mirror the core supervision's RestartBudget
 * discipline: at most N reloads per rolling window, with backoff, so a
 * boot-crash can never reload-loop.
 *
 * The window is ROLLING, not fixed: only crashes still inside the last
 * RENDERER_RECOVERY_WINDOW_MS count towards the budget, so a crash at the
 * 60s boundary never earns a fresh full budget.
 *
 * A failed launch ('launch-failed') is deliberately NOT recoverable: the
 * renderer could not start, so reloading the same window state would fail
 * again — the recovery budget must not be spent on it.
 */

export const RENDERER_RECOVERY_WINDOW_MS = 60_000
export const RENDERER_RECOVERY_MAX_ATTEMPTS = 3
export const RENDERER_RECOVERY_BASE_DELAY_MS = 250
export const RENDERER_RECOVERY_MAX_DELAY_MS = 2_000

/**
 * Reasons a renderer can disappear. Only genuine crashes warrant a reload;
 * a clean exit is not something a reload fixes, and a failed launch means
 * the renderer could not start — reloading the same window state would fail
 * again, so the recovery budget is not spent on it.
 */
export const RECOVERABLE_RENDERER_REASONS = new Set([
  'crashed',
  'oom',
  'killed',
  'abnormal-exit'
]) as ReadonlySet<string>

export interface RendererRecoveryState {
  /**
   * Timestamps (ms) of recent recoverable crashes, oldest-first. Bounded to
   * RENDERER_RECOVERY_MAX_ATTEMPTS entries so a long-lived app never grows
   * it unbounded.
   */
  recentCrashTimes: number[]
}

export const INITIAL_RENDERER_RECOVERY_STATE: RendererRecoveryState = {
  recentCrashTimes: []
}

export function isRecoverableRendererReason(reason: string): boolean {
  return RECOVERABLE_RENDERER_REASONS.has(reason)
}

/**
 * Decide whether (and when) to reload after a renderer crash.
 * Returns null when the crash is not recoverable or the reload budget for
 * the rolling window is exhausted.
 *
 * The window is rolling: only crashes still inside the last
 * RENDERER_RECOVERY_WINDOW_MS count, so crossing the boundary never grants a
 * fresh full budget. The backoff delay grows with the number of crashes
 * already inside the window (250/500/1000ms for the 1st/2nd/3rd crashes).
 */
export function planRendererRecovery(
  state: RendererRecoveryState,
  now: number,
  reason: string
): { delayMs: number; next: RendererRecoveryState } | null {
  if (!isRecoverableRendererReason(reason)) return null
  const cutoff = now - RENDERER_RECOVERY_WINDOW_MS
  const recent = state.recentCrashTimes.filter((crashAt) => crashAt > cutoff)
  if (recent.length >= RENDERER_RECOVERY_MAX_ATTEMPTS) return null
  const delayMs = Math.min(
    RENDERER_RECOVERY_MAX_DELAY_MS,
    RENDERER_RECOVERY_BASE_DELAY_MS * 2 ** recent.length
  )
  return {
    delayMs,
    next: {
      recentCrashTimes: [...recent, now].slice(-RENDERER_RECOVERY_MAX_ATTEMPTS)
    }
  }
}

/**
 * Orchestrates the recovery policy against the live app: records crashes,
 * schedules the (cancellable) reload on a backoff timer, and surfaces the
 * exhaustion dialog at most once per app run. Injectable seams keep this
 * testable without Electron (see renderer-recovery-supervisor.test.ts);
 * index.ts wires it with real setTimeout / dialog.
 */
export interface RendererRecoverySupervisorDeps {
  /** Actually reload the renderer. Wired in the app to re-check quitting and window identity. */
  reloadWindow: () => void
  /** Schedule fn after delayMs; returns a handle for cancelReload. */
  scheduleReload: (fn: () => void, delayMs: number) => unknown
  /** Cancel a previously scheduled reload. */
  cancelReload: (handle: unknown) => void
  /** Show the exhaustion dialog. Called at most once per app run. */
  showDialog: () => void
  /** Time source; injectable for deterministic tests. */
  now: () => number
}

export class RendererRecoverySupervisor {
  private state: RendererRecoveryState = INITIAL_RENDERER_RECOVERY_STATE
  private pendingReload: unknown = null
  private dialogShown = false

  constructor(private readonly deps: RendererRecoverySupervisorDeps) {}

  /**
   * Handle a render-process-gone event. Returns true when a reload was
   * scheduled. A crash while a reload is already pending cancels the stale
   * timer and reschedules, so at most one reload timer is ever pending.
   */
  renderProcessGone(reason: string): boolean {
    if (!isRecoverableRendererReason(reason)) return false
    const plan = planRendererRecovery(this.state, this.deps.now(), reason)
    if (!plan) {
      // Budget exhausted: a reload would just loop. The Python core is
      // untouched, so a full restart loses nothing. Warn the user once per
      // app run — subsequent exhausted crashes are logged, not dialogued.
      if (!this.dialogShown) {
        this.dialogShown = true
        this.deps.showDialog()
      }
      return false
    }
    this.state = plan.next
    this.cancelPendingReload()
    this.pendingReload = this.deps.scheduleReload(() => {
      this.pendingReload = null
      this.deps.reloadWindow()
    }, plan.delayMs)
    return true
  }

  /**
   * Cancel the pending reload, if any. Called on quit, window close/destroy,
   * and window replacement (top of createWindow) so a stale timer can never
   * fire against a window that is gone or no longer the current one.
   */
  cancelPendingReload(): void {
    if (this.pendingReload !== null) {
      this.deps.cancelReload(this.pendingReload)
      this.pendingReload = null
    }
  }
}
