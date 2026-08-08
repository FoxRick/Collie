/**
 * Renderer crash recovery policy: bounded auto-reload after the renderer
 * process is gone (crashed / OOM / killed / abnormal exit).
 *
 * The renderer is disposable — the Python core is a separate process, so a
 * reload fully rehydrates the UI from it (the white-screen-after-OOM failure
 * becomes a ~1s blip). We mirror the core supervision's RestartBudget
 * discipline: at most N reloads per rolling window, with backoff, so a
 * boot-crash can never reload-loop.
 */

export const RENDERER_RECOVERY_WINDOW_MS = 60_000
export const RENDERER_RECOVERY_MAX_ATTEMPTS = 3
export const RENDERER_RECOVERY_BASE_DELAY_MS = 250
export const RENDERER_RECOVERY_MAX_DELAY_MS = 2_000

/**
 * Reasons a renderer can disappear. Only genuine crashes warrant a reload;
 * a clean exit or a failed launch is not something a reload fixes.
 */
export const RECOVERABLE_RENDERER_REASONS = new Set([
  'crashed',
  'oom',
  'killed',
  'abnormal-exit',
  'launch-failed'
]) as ReadonlySet<string>

export interface RendererRecoveryState {
  attempts: number
  windowStartedAt: number | null
}

export const INITIAL_RENDERER_RECOVERY_STATE: RendererRecoveryState = {
  attempts: 0,
  windowStartedAt: null
}

export function isRecoverableRendererReason(reason: string): boolean {
  return RECOVERABLE_RENDERER_REASONS.has(reason)
}

/**
 * Decide whether (and when) to reload after a renderer crash.
 * Returns null when the crash is not recoverable or the reload budget for
 * the current rolling window is exhausted.
 */
export function planRendererRecovery(
  state: RendererRecoveryState,
  now: number,
  reason: string
): { delayMs: number; next: RendererRecoveryState } | null {
  if (!isRecoverableRendererReason(reason)) return null
  const startsNewWindow =
    state.windowStartedAt === null ||
    now - state.windowStartedAt >= RENDERER_RECOVERY_WINDOW_MS
  const attempts = startsNewWindow ? 0 : state.attempts
  if (attempts >= RENDERER_RECOVERY_MAX_ATTEMPTS) return null
  const delayMs = Math.min(
    RENDERER_RECOVERY_MAX_DELAY_MS,
    RENDERER_RECOVERY_BASE_DELAY_MS * 2 ** attempts
  )
  return {
    delayMs,
    next: {
      attempts: attempts + 1,
      windowStartedAt: startsNewWindow ? now : state.windowStartedAt
    }
  }
}
