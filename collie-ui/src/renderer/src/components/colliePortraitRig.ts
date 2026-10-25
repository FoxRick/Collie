export interface Point {
  x: number
  y: number
}
export interface RigPose {
  x: number
  y: number
  time: number
  mouth?: number
}
const ease = (v: number): number => { const t = Math.max(0, Math.min(1, v)); return t * t * (3 - 2 * t); }
export function attentionTarget(x: number, y: number): Point {
  const radius = Math.hypot(x, y)
  if (radius <= 0.16)
    return { x: 0, y: 0 }
  const strength = Math.min(1, (radius - 0.16) / 1.1)
  return { x: x / radius * strength, y: y / radius * strength }
}
/** A shallow 3D face surface: forehead and cheeks sit behind the muzzle.
* Project yaw/pitch around the head pivot, feathering to a fixed chest root.
* Unlike a translation, the nose moves substantially relative to the eyes. */
export function rigPoint(x: number, y: number, pose: RigPose): Point {
  const head = 1 - ease((y - 250) / 90)
  const face = Math.exp(-(((x - 190) / 110) ** 2 + ((y - 170) / 130) ** 2))
  const muzzle = Math.exp(-(((x - 184) / 65) ** 2 + ((y - 195) / 60) ** 2))
  const depth = 30 * face + 95 * muzzle
  const yaw = pose.x * 0.62, pitch = -pose.y * 0.38
  const rx = (x - 190) * Math.cos(yaw) + depth * Math.sin(yaw)
  const rz = -(x - 190) * Math.sin(yaw) + depth * Math.cos(yaw)
  const ry = (y - 195) * Math.cos(pitch) - rz * Math.sin(pitch)
  const jaw = Math.exp(-(((y - 255) / 45) ** 2)) * Math.exp(-(((x - 187) / 65) ** 2))
  const eye = Math.exp(-(((x - 145) / 20) ** 2 + ((y - 130) / 18) ** 2)) + Math.exp(-(((x - 235) / 20) ** 2 + ((y - 136) / 18) ** 2))
  return {
    x: x + (190 + rx - x) * head + eye * pose.x * 2,
    y: y + (195 + ry - y) * head + jaw * (pose.mouth || 0) * 18
      + Math.sin(pose.time * 1.8) * 0.65 * Math.max(0, (384 - y) / 384)
  }
}
const masks = new WeakMap<HTMLCanvasElement, boolean[]>()
/** Ignore transparent triangles in local eye/mouth layers. Computed once. */
function occupied(texture: HTMLCanvasElement): boolean[] {
  const cached = masks.get(texture)
  if (cached)
    return cached
  const alpha = texture.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, 384, 384).data
  const cells: boolean[] = []
  for (let row = 0; row < 16; row++)
    for (let col = 0; col < 12; col++) {
      let visible = false
      for (let y = row * 24; y <= Math.min(383, (row + 1) * 24) && !visible; y += 2) {
        for (let x = col * 32; x <= Math.min(383, (col + 1) * 32); x += 2)
          if (alpha[(y * 384 + x) * 4 + 3] > 0) {
            visible = true
            break
          }
      }
      cells.push(visible)
    }
  masks.set(texture, cells)
  return cells
}
export function drawRig(ctx: CanvasRenderingContext2D, texture: HTMLCanvasElement, pose: RigPose, alpha = 1): void {
  const points: Point[] = []
  for (let row = 0; row <= 16; row++)
    for (let col = 0; col <= 12; col++)
      points.push(rigPoint(col * 32, row * 24, pose))
  const visible = occupied(texture), overlap = 1.25 / Math.max(0.1, ctx.getTransform().a)
  function triangle(source: Point[], target: Point[]): void {
    const [s0, s1, s2] = source, [a, b, c] = target
    const det = (s1.x - s0.x) * (s2.y - s0.y) - (s2.x - s0.x) * (s1.y - s0.y)
    const m11 = ((b.x - a.x) * (s2.y - s0.y) - (c.x - a.x) * (s1.y - s0.y)) / det
    const m12 = ((b.y - a.y) * (s2.y - s0.y) - (c.y - a.y) * (s1.y - s0.y)) / det
    const m21 = ((c.x - a.x) * (s1.x - s0.x) - (b.x - a.x) * (s2.x - s0.x)) / det
    const m22 = ((c.y - a.y) * (s1.x - s0.x) - (b.y - a.y) * (s2.x - s0.x)) / det
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.beginPath()
    const cx = (a.x + b.x + c.x) / 3, cy = (a.y + b.y + c.y) / 3
    for (const [i, p] of target.entries()) {
      const dx = p.x - cx, dy = p.y - cy, length = Math.max(0.1, Math.hypot(dx, dy))
      const px = p.x + dx / length * overlap, py = p.y + dy / length * overlap
      if (i === 0)
        ctx.moveTo(px, py)
      else
        ctx.lineTo(px, py)
    }
    ctx.closePath()
    ctx.clip()
    ctx.transform(m11, m12, m21, m22, a.x - m11 * s0.x - m21 * s0.y, a.y - m12 * s0.x - m22 * s0.y)
    ctx.drawImage(texture, 0, 0)
    ctx.restore()
  }
  for (let row = 0; row < 16; row++)
    for (let col = 0; col < 12; col++) {
      if (!visible[row * 12 + col])
        continue
      const i = row * 13 + col, a = { x: col * 32, y: row * 24 }, b = { x: (col + 1) * 32, y: row * 24 }
      const c = { x: col * 32, y: (row + 1) * 24 }, d = { x: (col + 1) * 32, y: (row + 1) * 24 }
      triangle([a, b, c], [points[i], points[i + 1], points[i + 13]])
      triangle([b, d, c], [points[i + 1], points[i + 14], points[i + 13]])
    }
}
