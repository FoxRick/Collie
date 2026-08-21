export const LOOK_DIRECTION_COUNT = 16

/**
 * Quantizes a pointer vector into the nearest clockwise look direction:
 * 0 is up, 4 is screen-right. Used by the state machine to decide when the
 * portrait enters `pointer_look`; the renderer itself tracks the raw vector
 * for continuous gaze.
 */
export function quantizePortraitPointer(
  x: number,
  y: number,
  deadZoneRadius = 0.12
): number | null {
  if (Math.hypot(x, y) <= deadZoneRadius) return null
  const degrees = (Math.atan2(x, -y) * 180) / Math.PI
  return ((Math.round(degrees / 22.5) % LOOK_DIRECTION_COUNT) + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
}

/**
 * Shortest signed delta in a 16-way circle: from 15.9 to 0.1 is +0.2 (forward
 * across zero), from 0.1 to 15.9 is -0.2 (backward across zero). Range (-8, 8].
 */
export function wrapDirectionDelta(from: number, to: number): number {
  return (
    ((((to - from) % LOOK_DIRECTION_COUNT) + LOOK_DIRECTION_COUNT + LOOK_DIRECTION_COUNT / 2) %
      LOOK_DIRECTION_COUNT) -
    LOOK_DIRECTION_COUNT / 2
  )
}

/**
 * Eased chase of ONE continuous direction value toward target. The lerp keeps
 * it moving every frame; wrap handling means it never sweeps across the face.
 * A lerp of 1 reaches the target exactly (used in tests).
 */
export function chaseDirection(current: number, target: number, lerp = 0.16): number {
  return current + wrapDirectionDelta(current, target) * lerp
}

/** Exponential smoothing factor for a rate per second over a dt frame. */
export function smoothFactor(rate: number, dt: number): number {
  return 1 - Math.exp(-rate * dt)
}
