import type { PortraitState } from './portraitStates'

export const EXPRESSION_ASSETS = {
  mouth: new URL('../assets/portrait/expression-mouth-v2.webp', import.meta.url).href,
  eyes: new URL('../assets/portrait/expression-eyes-v2.webp', import.meta.url).href,
  paw: new URL('../assets/portrait/expression-paw-v2.webp', import.meta.url).href,
  ears: new URL('../assets/portrait/expression-ears-v2.webp', import.meta.url).href
}
export interface ExpressionPose {
  mouth: number
  lids: number
  ears: number
  paw: number
  time: number
}
export const ease = (value: number): number => {
  const t = Math.max(0, Math.min(1, value))
  return t * t * (3 - 2 * t)
}

/** Channels keep their value when interrupted. No outgoing head or texture allocation. */
export function expressionTarget(state: PortraitState, time: number, entered: number): Omit<ExpressionPose, 'time'> {
  const wave = ease((entered - .1) / .28) * (1 - ease((entered - .95) / .55))
  switch (state) {
    case 'sleepy': return { mouth: .12, lids: 1, ears: -.6, paw: 0 }
    case 'error': return { mouth: .16, lids: .12, ears: -.5, paw: 0 }
    case 'waiting': return { mouth: .45, lids: .06, ears: .4, paw: .65 * wave }
    case 'thinking': return { mouth: .35, lids: .2, ears: .35, paw: 0 }
    case 'review': return { mouth: .4, lids: .32, ears: .65, paw: 0 }
    case 'working': return { mouth: .52, lids: .08, ears: .6, paw: 0 }
    case 'delegating': return { mouth: .75, lids: .06, ears: .85, paw: .85 * wave }
    case 'listening': return { mouth: .6, lids: .08, ears: .8, paw: 0 }
    case 'writing': return { mouth: .61 + Math.sin(time * 5.5) * .12 * (.5 + .5 * Math.sin(time * 1.3)), lids: .08, ears: .35, paw: 0 }
    case 'attentive': return { mouth: .65, lids: 0, ears: .85, paw: 0 }
    case 'completion':
    case 'click_reaction':
    case 'happy_reaction': return { mouth: .97, lids: .28, ears: 1, paw: wave }
    default: return { mouth: .65, lids: 0, ears: .12, paw: 0 }
  }
}
export function stepExpression(pose: ExpressionPose, state: PortraitState, dt: number, entered: number): void {
  const target = expressionTarget(state, pose.time, entered)
  const face = 1 - Math.exp(-10 * dt), ears = 1 - Math.exp(-6 * dt)
  pose.mouth += (target.mouth - pose.mouth) * face
  pose.lids += (target.lids - pose.lids) * face
  pose.ears += (target.ears - pose.ears) * ears
  pose.paw += (target.paw - pose.paw) * (1 - Math.exp(-14 * dt))
}
export function blinkAt(time: number): number {
  const cycle = Math.floor(time / 14), phase = time - cycle * 14
  const jitter = (cycle % 3) * .11
  const pulse = (peak: number): number => {
    const offset = phase - peak
    return offset < 0 ? ease(1 + offset / .1) : 1 - ease(offset / .16)
  }
  return Math.max(pulse(2.8 + jitter), pulse(6.1 - jitter), pulse(9.7 + jitter), pulse(12.8),
    cycle % 4 === 2 ? pulse(3.15 + jitter) : 0)
}

/** Brief ear gestures with a quiet hold between them, not a constant wobble. */
export function earTwitchAt(time: number): number {
  const phase = time % 10.3
  const pulse = (peak: number): number => {
    const offset = phase - peak
    return offset < 0 ? ease(1 + offset / .22) : 1 - ease(offset / .65)
  }
  return .75 * pulse(1.5) - .35 * pulse(4.9) + .55 * pulse(8.2)
}

/** One finished mouth frame + one eye patch. No whole-head crossfade. */
export function drawExpression(ctx: CanvasRenderingContext2D, images: HTMLImageElement[], pose: ExpressionPose, reducedMotion = false): void {
  const index = Math.max(0, Math.min(48, Math.round(pose.mouth * 48)))
  const sx = index % 7 * 256, sy = Math.floor(index / 7) * 256
  ctx.clearRect(0, 0, 384, 384)
  ctx.save()
  // Smaller fixed portrait, optically centered toward the muzzle. Only the
  // lower chest extends to the ring; shrinking never clips the ear tips.
  ctx.translate(28, 22)
  ctx.scale(.9, .9)
  // Overlap inside identical fur pixels so fractional-DPI edge coverage
  // cannot leave a hairline between the adjacent image regions.
  ctx.drawImage(images[0], sx, sy + 79, 256, 163 / 1.5, 0, 118.5, 384, 163)
  ctx.drawImage(images[0], sx, sy + 280 / 1.5, 256, 104 / 1.5, 0, 280, 384, 130)
  // An inverse-sampled top strip meets the fixed face at exactly y=120.
  const twitch = reducedMotion || pose.lids > .9 ? 0 : earTwitchAt(pose.time)
  const ear = Math.max(0, Math.min(32, Math.round((pose.ears + twitch + .7) / 1.85 * 32)))
  ctx.drawImage(images[3], ear % 11 * 256, Math.floor(ear / 11) * 80, 256, 80, 0, 0, 384, 120)
  const lids = Math.max(pose.lids, reducedMotion ? 0 : blinkAt(pose.time))
  const eye = Math.max(0, Math.min(32, Math.round(lids * 32)))
  if (eye > 0) ctx.drawImage(images[1], eye % 11 * 156, Math.floor(eye / 11) * 74, 156, 74, 77, 126, 156, 74)
  if (pose.paw > .001) {
    ctx.save()
    ctx.translate(68, 384)
    ctx.rotate((1 - pose.paw) * -.5 + Math.sin(pose.time * 12) * .025 * pose.paw)
    ctx.translate(-68, -384 + (1 - pose.paw) * 155)
    ctx.drawImage(images[2], 0, 0, 384, 384)
    ctx.restore()
  }
  ctx.restore()
}
