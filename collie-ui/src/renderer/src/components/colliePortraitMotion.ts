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

/**
 * Horizontal framing offset that centers a cell's alpha content bbox on the
 * canvas, clamped so the content never clips at either side edge.
 *
 * bboxLeftFrac/bboxRightFrac are the measured content span (0..1 of the
 * cell), drawWidth the scaled drawn cell width, scale the framing scale,
 * frameSize the logical canvas side. Returns a dx to add to the X translate.
 *
 * Applied PER CELL (each frame's own bbox) rather than to a union bbox — a
 * single union offset only centers the average and leaves per-frame drift.
 */
export function horizontalFramingOffset(
  bboxLeftFrac: number,
  bboxRightFrac: number,
  drawWidth: number,
  scale: number,
  frameSize: number
): number {
  const centerFrac = (bboxLeftFrac + bboxRightFrac) / 2
  let dx = (0.5 - centerFrac) * drawWidth * scale
  const contentHalf = ((bboxRightFrac - bboxLeftFrac) * drawWidth * scale) / 2
  if (contentHalf < frameSize / 2) {
    const minCenter = contentHalf
    const maxCenter = frameSize - contentHalf
    dx = Math.max(minCenter - frameSize / 2, Math.min(maxCenter - frameSize / 2, dx))
  }
  return dx
}
