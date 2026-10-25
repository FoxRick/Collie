import type { Point } from './colliePortraitRig'
export interface FaceAnchor {
  texture: HTMLCanvasElement
  points: Point[]
  features: Point[]
}
export interface FaceArtwork {
  neutral: FaceAnchor
  mouth: FaceAnchor
  blink: FaceAnchor
  arm: HTMLCanvasElement
}
// Painted landmarks: two ear tips, crown, two eyes, nose, two muzzle edges,
// lip, chin, cheeks, neck and shoulders. Coordinates are in 384px cells.
const front = [68, 22, 323, 57, 194, 68, 145, 130, 235, 136, 184, 183, 137, 207, 232, 211, 184, 235, 187, 254, 91, 191, 281, 198, 107, 271, 278, 271, 61, 305, 330, 305]
const features = (data: number[]): Point[] => Array.from({ length: data.length / 2 }, (_, i) => ({ x: data[i * 2], y: data[i * 2 + 1] }))
export const NEUTRAL_FEATURES = features(front)
/** Align the painted smile and eyelids to the neutral portrait once at load.
 * These grids are asset registration, not a blend between whole head poses. */
export function faceGrid(landmarks: Point[], bounds: (y: number) => [
  number,
  number
]): Point[] {
  const p = landmarks
  const ys = [0, Math.min(p[0].y, p[1].y), p[2].y, (p[3].y + p[4].y) / 2, p[5].y, p[8].y, p[9].y, (p[12].y + p[13].y) / 2, 384]
  for (let i = 1; i < ys.length - 1; i++)
    ys[i] = Math.max(ys[i], ys[i - 1] + 12)
  const xs = [
    [96, 192, 288], [p[2].x - 65, p[2].x, p[2].x + 65], [p[2].x - 65, p[2].x, p[2].x + 65],
    [p[3].x, (p[3].x + p[4].x) / 2, p[4].x], [p[6].x, p[5].x, p[7].x],
    [p[6].x, p[8].x, p[7].x], [p[9].x - 42, p[9].x, p[9].x + 42],
    [p[12].x, (p[12].x + p[13].x) / 2, p[13].x], [96, 192, 288]
  ]
  const points: Point[] = []
  for (let band = 0; band < 8; band++)
    for (let step = 0; step < 2; step++) {
      const t = step / 2, y = ys[band] + (ys[band + 1] - ys[band]) * t, [lo, hi] = bounds(y)
      const left = Math.max(1, Math.min(360, lo)), right = Math.max(left + 18, Math.min(383, hi))
      const inner = xs[band].map((v, i) => v + (xs[band + 1][i] - v) * t)
      const middle = Math.max(left + 6, Math.min(right - 6, inner[1]))
      const a = Math.max(left + 2, Math.min(middle - 2, inner[0])), b = Math.max(middle + 2, Math.min(right - 2, inner[2]))
      for (const x of [0, left, a, middle, b, right, 384])
        points.push({ x, y })
    }
  for (const x of [0, 48, 96, 192, 288, 336, 384])
    points.push({ x, y: 384 })
  return points
}
function crop(image: HTMLImageElement, cols: number, rows: number, cell: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = 384
  c.height = 384
  const sw = image.naturalWidth / cols, sh = image.naturalHeight / rows
  const context = c.getContext('2d', { willReadFrequently: true })!
  context.drawImage(image, cell % cols * sw, Math.floor(cell / cols) * sh, sw, sh, 0, 0, 384, 384)
  // The original atlas has magenta matte spill on some transparent fur edges.
  // Desaturate that spill only at the alpha boundary, preserving skin/eye color.
  const pixels = context.getImageData(0, 0, 384, 384)
  const data = pixels.data
  for (let i = 0; i < data.length; i += 4) {
    const red = data[i], green = data[i + 1], blue = data[i + 2]
    const edge = !data[i - 1] || !data[i + 7] || !data[i - 1533] || !data[i + 1539]
    if (edge && data[i + 3] && red - green > 18 && blue - green > 18) {
      data[i] = green
      data[i + 2] = green
    }
  }
  context.putImageData(pixels, 0, 0)
  return c
}
function anchor(texture: HTMLCanvasElement, landmarks: Point[]): FaceAnchor {
  const data = texture.getContext('2d', { willReadFrequently: true })!.getImageData(0, 0, 384, 384).data
  const contour = (y: number): [
    number,
    number
  ] => {
    let left = 384, right = 0
    const row = Math.max(0, Math.min(383, Math.round(y)))
    for (let x = 0; x < 384; x++)
      if (data[(row * 384 + x) * 4 + 3] > 24) {
        left = Math.min(left, x)
        right = Math.max(right, x)
      }
    return right > left ? [left, right] : [48, 336]
  }
  return { texture, features: landmarks, points: faceGrid(landmarks, contour) }
}
export function prepareFaceArtwork(pointer: HTMLImageElement, click: HTMLImageElement, idle: HTMLImageElement, logo?: HTMLImageElement): FaceArtwork {
  const neutral = anchor(crop(pointer, 4, 4, 0), NEUTRAL_FEATURES)
  // The actual app logo supplies the broad, tongue-out smile.
  const mouth = logo
    ? anchor(crop(logo, 1, 1, 0), features([90, 29, 339, 73, 194, 93, 123, 156, 214, 171, 131, 216, 97, 245, 227, 239, 135, 254, 154, 337, 90, 209, 290, 235, 100, 314, 290, 314, 65, 360, 345, 360]))
    : anchor(crop(click, 2, 2, 0), features([68, 60, 322, 73, 196, 111, 146, 173, 231, 174, 175, 235, 134, 263, 219, 260, 176, 275, 175, 317, 100, 221, 280, 232, 111, 308, 279, 310, 72, 338, 330, 338]))
  const mask = document.createElement('canvas')
  mask.width = 384
  mask.height = 384
  const mg = mask.getContext('2d')!, grad = mg.createRadialGradient(logo ? 158 : 176, 286, logo ? 65 : 28, logo ? 158 : 176, 286, logo ? 95 : 65)
  grad.addColorStop(0, 'white')
  grad.addColorStop(.7, 'white')
  grad.addColorStop(1, 'transparent')
  mg.fillStyle = grad
  mg.fillRect(0, 0, 384, 384)
  const g = mouth.texture.getContext('2d')!
  g.globalCompositeOperation = 'destination-in'
  g.drawImage(mask, 0, 0)
  const blink = anchor(crop(idle, 3, 2, 2), features([65, 34, 324, 63, 182, 91, 122, 165, 206, 178, 130, 231, 93, 254, 174, 260, 132, 271, 145, 285, 80, 219, 278, 231, 107, 289, 280, 289, 63, 324, 330, 324]))
  const eyeMask = document.createElement('canvas')
  eyeMask.width = 384
  eyeMask.height = 384
  const eg = eyeMask.getContext('2d')!
  for (const [x, y] of [[122, 165], [206, 178]]) {
    const a = eg.createRadialGradient(x, y, 10, x, y, 29)
    a.addColorStop(0, 'white')
    a.addColorStop(.65, 'white')
    a.addColorStop(1, 'transparent')
    eg.fillStyle = a
    eg.fillRect(x - 30, y - 30, 60, 60)
  }
  const bg = blink.texture.getContext('2d')!
  bg.globalCompositeOperation = 'destination-in'
  bg.drawImage(eyeMask, 0, 0)
  const arm = crop(click, 2, 2, 2), ag = arm.getContext('2d')!, armMask = document.createElement('canvas')
  armMask.width = 384
  armMask.height = 384
  const am = armMask.getContext('2d')!
  am.beginPath()
  am.moveTo(31, 384)
  am.lineTo(31, 277)
  am.quadraticCurveTo(20, 237, 40, 224)
  am.quadraticCurveTo(65, 207, 86, 232)
  am.quadraticCurveTo(104, 252, 96, 281)
  am.lineTo(105, 384)
  am.closePath()
  am.fill()
  ag.globalCompositeOperation = 'destination-in'
  ag.drawImage(armMask, 0, 0)
  const mouthLayer = document.createElement('canvas'), blinkLayer = document.createElement('canvas')
  mouthLayer.width = blinkLayer.width = 384
  mouthLayer.height = blinkLayer.height = 384
  const mouthContext = mouthLayer.getContext('2d')!
  if (logo) {
    // Match the nose anchor by translation. Warping the logo's already-open
    // jaw into the closed-mouth chin interval squashes its tongue into a line.
    mouthContext.drawImage(mouth.texture, 53, -33)
    mouthContext.globalCompositeOperation = 'destination-in'
    mouthContext.drawImage(neutral.texture, 0, 0)
    mouthContext.globalCompositeOperation = 'source-over'
  } else warpFace(mouthContext, mouth, neutral.points)
  warpFace(blinkLayer.getContext('2d')!, blink, neutral.points)
  return { neutral, mouth: { ...neutral, texture: mouthLayer }, blink: { ...neutral, texture: blinkLayer }, arm }
}
export function warpFace(ctx: CanvasRenderingContext2D, anchor: FaceAnchor, destination: Point[], alpha = 1): void {
  const overlap = 1.25 / Math.max(.1, ctx.getTransform().a)
  function triangle(indices: number[]): void {
    const [s0, s1, s2] = indices.map(i => anchor.points[i]), [a, b, c] = indices.map(i => destination[i])
    const det = (s1.x - s0.x) * (s2.y - s0.y) - (s2.x - s0.x) * (s1.y - s0.y)
    if (Math.abs(det) < .001)
      return
    const m11 = ((b.x - a.x) * (s2.y - s0.y) - (c.x - a.x) * (s1.y - s0.y)) / det, m12 = ((b.y - a.y) * (s2.y - s0.y) - (c.y - a.y) * (s1.y - s0.y)) / det
    const m21 = ((c.x - a.x) * (s1.x - s0.x) - (b.x - a.x) * (s2.x - s0.x)) / det, m22 = ((c.y - a.y) * (s1.x - s0.x) - (b.y - a.y) * (s2.x - s0.x)) / det
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.beginPath()
    const cx = (a.x + b.x + c.x) / 3, cy = (a.y + b.y + c.y) / 3
    for (const [i, p] of [a, b, c].entries()) {
      const dx = p.x - cx, dy = p.y - cy, l = Math.max(.1, Math.hypot(dx, dy)), x = p.x + dx / l * overlap, y = p.y + dy / l * overlap
      if (i === 0)
        ctx.moveTo(x, y)
      else
        ctx.lineTo(x, y)
    }
    ctx.closePath()
    ctx.clip()
    ctx.transform(m11, m12, m21, m22, a.x - m11 * s0.x - m21 * s0.y, a.y - m12 * s0.x - m22 * s0.y)
    ctx.drawImage(anchor.texture, 0, 0)
    ctx.restore()
  }
  for (let row = 0; row < 16; row++)
    for (let col = 0; col < 6; col++) {
      const i = row * 7 + col
      triangle([i, i + 1, i + 7])
      triangle([i + 1, i + 8, i + 7])
    }
}
