export const LOOK_DIRECTION_COUNT = 16
export const LOOK_DIRECTION_STEP_MS = 72

/** Returns clockwise look direction: 0 is up, 4 is screen-right. */
export function quantizePortraitPointer(
  x: number,
  y: number,
  deadZoneRadius = 0.18
): number | null {
  if (Math.hypot(x, y) <= deadZoneRadius) return null
  const degrees = (Math.atan2(x, -y) * 180) / Math.PI
  return ((Math.round(degrees / 22.5) % LOOK_DIRECTION_COUNT) + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
}

/** Eases through one neighbouring 16-way direction; it never snaps across the face. */
export function stepPortraitDirection(current: number, target: number): number {
  const clockwise = (target - current + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
  if (clockwise === 0) return current
  return (current + (clockwise <= LOOK_DIRECTION_COUNT / 2 ? 1 : -1) + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
}
