/** A single texture, continuous geometry, and a fixed chest root. Units are
 * source pixels in the existing 384px portrait, independent of display DPR. */
export interface RigPose { x: number; y: number; joy: number; focus: number; concern: number; time: number }
export interface Point { x: number; y: number }
const ease = (v: number): number => { const t = Math.max(0, Math.min(1, v)); return t * t * (3 - 2 * t) }

export function attentionTarget(x: number, y: number): Point {
  const radius = Math.hypot(x, y)
  if (radius <= 0.16) return { x: 0, y: 0 }
  const strength = Math.min(1, (radius - 0.16) / 1.1)
  return { x: x / radius * strength, y: y / radius * strength }
}

export function rigPoint(x: number, y: number, pose: RigPose): Point {
  const head = 1 - ease((y - 235) / 110)
  const face = Math.exp(-(((x - 165) / 95) ** 2 + ((y - 176) / 105) ** 2))
  const ear = (1 - ease((y - 55) / 70)) * Math.min(1, Math.abs(x - 190) / 100)
  const breath = Math.sin(pose.time * 1.8) * 0.8
  const roll = pose.x * 0.035 + pose.concern * -0.035 + pose.joy * 0.025
  // The muzzle uses one smooth region; no independently sliding nose/mouth.
  return {
    x: x + head * (pose.x * (10 + 6 * face) - (y - 220) * roll)
      + ear * Math.sin(pose.time * 2.1 + x * 0.02) * 0.65,
    y: y + head * (pose.y * (8 + 3 * face) + (x - 180) * roll - pose.joy * 3)
      - ear * pose.joy * 4 + breath * ease((384 - y) / 120)
      + pose.focus * head * 2
  }
}

/** Piecewise affine Canvas mesh: 12×16 quads, no GPU/runtime dependency.
 * Shared vertex positions keep the texture continuous through state changes. */
export function drawRig(ctx: CanvasRenderingContext2D, texture: CanvasImageSource, pose: RigPose): void {
  const cols = 12, rows = 16, w = 384 / cols, h = 384 / rows
  const points: Point[] = []
  for (let row = 0; row <= rows; row++) {
    for (let col = 0; col <= cols; col++) points.push(rigPoint(col * w, row * h, pose))
  }
  function triangle(s0: Point, s1: Point, s2: Point, a: Point, b: Point, c: Point): void {
    const det = (s1.x - s0.x) * (s2.y - s0.y) - (s2.x - s0.x) * (s1.y - s0.y)
    const m11 = ((b.x - a.x) * (s2.y - s0.y) - (c.x - a.x) * (s1.y - s0.y)) / det
    const m12 = ((b.y - a.y) * (s2.y - s0.y) - (c.y - a.y) * (s1.y - s0.y)) / det
    const m21 = ((c.x - a.x) * (s1.x - s0.x) - (b.x - a.x) * (s2.x - s0.x)) / det
    const m22 = ((c.y - a.y) * (s1.x - s0.x) - (b.y - a.y) * (s2.x - s0.x)) / det
    ctx.save()
    ctx.beginPath()
    // Subpixel overlap prevents Canvas antialias cracks between triangles.
    const cx = (a.x + b.x + c.x) / 3, cy = (a.y + b.y + c.y) / 3
    for (const [index, p] of [a, b, c].entries()) {
      const dx = p.x - cx, dy = p.y - cy, length = Math.hypot(dx, dy)
      const px = p.x + dx / length * 0.45, py = p.y + dy / length * 0.45
      if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
    }
    ctx.closePath(); ctx.clip()
    ctx.transform(m11, m12, m21, m22, a.x - m11 * s0.x - m21 * s0.y, a.y - m12 * s0.x - m22 * s0.y)
    ctx.drawImage(texture, 0, 0, 384, 384)
    ctx.restore()
  }
  for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {
    const i = row * (cols + 1) + col
    const a = { x: col * w, y: row * h }, b = { x: (col + 1) * w, y: row * h }
    const c = { x: col * w, y: (row + 1) * h }, d = { x: (col + 1) * w, y: (row + 1) * h }
    triangle(a, b, c, points[i], points[i + 1], points[i + cols + 1])
    triangle(b, d, c, points[i + 1], points[i + cols + 2], points[i + cols + 1])
  }
}
