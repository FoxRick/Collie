import { useEffect, useRef } from 'react'
import { smoothFactor } from './colliePortraitMotion'
import { attentionTarget, drawRig, type RigPose } from './colliePortraitRig'
import type { PortraitState } from './portraitStates'

interface Props {
  state: PortraitState
  reducedMotion: boolean
  fallbackSrc: string
  className?: string
}
const pawSrc = new URL('../assets/portrait/paw-front-brand.webp', import.meta.url).href

/** One renderer and one silhouette. The still is visible only until the first
 * successful draw, or if Canvas/image loading fails. No whole-head blends. */
export default function ColliePortraitFrame({ state, reducedMotion, fallbackSrc, className }: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fallbackRef = useRef<HTMLImageElement>(null)
  const stateRef = useRef(state)
  const poseRef = useRef<RigPose>({ x: 0, y: 0, joy: 0, focus: 0, concern: 0, time: 0 })
  useEffect(() => { stateRef.current = state }, [state])

  useEffect(() => {
    const canvas = canvasRef.current
    const fallback = fallbackRef.current
    if (!canvas || !fallback) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let disposed = false, ready = false, lost = false
    let raf: number | null = null, last = 0, lastDraw = 0
    let target = { x: 0, y: 0 }
    let reaction = 0, reactionState = false
    const texture = new Image(), paw = new Image()
    const pose = poseRef.current
    const stop = (): void => { if (raf !== null) cancelAnimationFrame(raf); raf = null; last = 0; lastDraw = 0 }
    const draw = (): void => {
      // Budget pixels to actual displayed size, including display scaling.
      const side = Math.max(1, Math.round(Math.min(2, window.devicePixelRatio || 1) * (canvas.getBoundingClientRect().width || 136)))
      if (canvas.width !== side || canvas.height !== side) { canvas.width = side; canvas.height = side }
      ctx.setTransform(side / 384, 0, 0, side / 384, 0, 0)
      ctx.clearRect(0, 0, 384, 384)
      drawRig(ctx, texture, pose)
      if (pose.joy > 0.001 && paw.complete && paw.naturalWidth) {
        ctx.save()
        ctx.translate(90, 460 - 150 * pose.joy)
        ctx.rotate(-0.12 * pose.joy)
        ctx.drawImage(paw, -65, -130, 130, 199)
        ctx.restore()
      }
      fallback.hidden = true
      canvas.hidden = false
    }
    const frame = (now: number): void => {
      raf = null
      if (disposed || document.hidden || lost || !ready) return
      // 60 Hz ceiling even on 120/144 Hz displays; smoothing uses elapsed time.
      if (lastDraw && now - lastDraw < 1000 / 60 - 1) { raf = requestAnimationFrame(frame); return }
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60
      last = now; lastDraw = now
      pose.time += dt
      const current = stateRef.current
      const clicked = current === 'click_reaction' || current === 'completion'
      if (clicked && !reactionState) reaction = 0
      reactionState = clicked
      if (clicked) reaction += dt
      // Anticipation, rise, hold, then return; the shared pose never resets.
      const joy = clicked && reaction > 0.10 && reaction < 0.62 ? 1 : 0
      const busy = ['working', 'review', 'deep_work_glasses'].includes(current)
      const attention = current === 'sleepy' ? 0 : busy ? 0.35 : 1
      const f = smoothFactor(10, dt)
      pose.x += (target.x * attention - pose.x) * f
      pose.y += (target.y * attention - pose.y) * f
      pose.joy += (joy - pose.joy) * smoothFactor(15, dt)
      pose.focus += ((busy ? 1 : 0) - pose.focus) * f
      pose.concern += ((current === 'error' || current === 'waiting' ? 1 : 0) - pose.concern) * f
      try { draw() } catch { fallback.hidden = false; canvas.hidden = true; return }
      if (!reducedMotion) raf = requestAnimationFrame(frame)
    }
    const start = (): void => {
      stop()
      if (!disposed && ready && !document.hidden && !lost) {
        if (reducedMotion) {
          pose.x = 0; pose.y = 0; pose.joy = 0; pose.time = 0
          draw()
        } else raf = requestAnimationFrame(frame)
      }
    }
    const move = (event: PointerEvent): void => {
      const rect = canvas.getBoundingClientRect(), radius = Math.max(1, rect.width / 2)
      target = attentionTarget((event.clientX - rect.left - radius) / radius, (event.clientY - rect.top - rect.height / 2) / radius)
    }
    const leave = (): void => { target = { x: 0, y: 0 } }
    const contextLost = (event: Event): void => { event.preventDefault(); lost = true; stop(); fallback.hidden = false; canvas.hidden = true }
    const contextRestored = (): void => { lost = false; start() }
    texture.onload = () => { if (!disposed) { ready = true; start() } }
    texture.onerror = () => { ready = false; stop(); fallback.hidden = false; canvas.hidden = true }
    texture.src = fallbackSrc
    paw.src = pawSrc
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('blur', leave)
    document.addEventListener('pointerleave', leave)
    document.addEventListener('visibilitychange', start)
    canvas.addEventListener('contextlost', contextLost)
    canvas.addEventListener('contextrestored', contextRestored)
    return () => {
      disposed = true; stop()
      window.removeEventListener('pointermove', move)
      window.removeEventListener('blur', leave)
      document.removeEventListener('pointerleave', leave)
      document.removeEventListener('visibilitychange', start)
      canvas.removeEventListener('contextlost', contextLost)
      canvas.removeEventListener('contextrestored', contextRestored)
      texture.onload = null; texture.onerror = null
    }
  }, [fallbackSrc, reducedMotion])

  return <>
    <img ref={fallbackRef} className={className} src={fallbackSrc} alt="" draggable={false} aria-hidden="true" />
    <canvas ref={canvasRef} className={className} hidden aria-hidden="true" />
  </>
}
