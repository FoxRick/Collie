import { useEffect, useRef } from 'react'
import { drawExpression, EXPRESSION_ASSETS, expressionTarget, stepExpression, type ExpressionPose } from './collieExpressions'
import type { PortraitState } from './portraitStates'

interface Props {
  state: PortraitState
  reducedMotion: boolean
  fallbackSrc: string
  className?: string
}

export default function ColliePortraitFrame({ state, reducedMotion, fallbackSrc, className }: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fallbackRef = useRef<HTMLImageElement>(null)
  const stateRef = useRef(state)
  const redrawRef = useRef<(() => void) | null>(null)
  const poseRef = useRef<ExpressionPose>({ mouth: .65, lids: 0, ears: .12, paw: 0, time: 0 })
  useEffect(() => { stateRef.current = state; redrawRef.current?.() }, [state])

  useEffect(() => {
    const canvas = canvasRef.current, fallback = fallbackRef.current
    if (!canvas || !fallback) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let disposed = false, lost = false, ready = false, raf: number | null = null
    let previousTime = 0, nextDraw = 0, entered = 0, current = stateRef.current
    const pose = poseRef.current
    const images = Object.values(EXPRESSION_ASSETS).map(() => new Image())
    const stop = (): void => {
      if (raf !== null) cancelAnimationFrame(raf)
      raf = null
      previousTime = 0
      nextDraw = 0
    }
    const showFallback = (): void => { stop(); fallback.hidden = false; canvas.hidden = true }
    const draw = (): void => {
      const side = Math.max(1, Math.round(Math.min(2, window.devicePixelRatio || 1) * (canvas.getBoundingClientRect().width || 136)))
      if (canvas.width !== side || canvas.height !== side) { canvas.width = side; canvas.height = side }
      ctx.setTransform(side / 384, 0, 0, side / 384, 0, 0)
      drawExpression(ctx, images, pose, reducedMotion)
      canvas.dataset.expression = current
      canvas.dataset.mouth = pose.mouth.toFixed(3)
      canvas.dataset.lids = pose.lids.toFixed(3)
      canvas.dataset.wave = pose.paw.toFixed(3)
      canvas.dataset.clock = pose.time.toFixed(3)
      fallback.hidden = true
      canvas.hidden = false
    }
    const frame = (now: number): void => {
      raf = null
      if (disposed || document.hidden || lost || !ready) return
      if (now < nextDraw - .5) { raf = requestAnimationFrame(frame); return }
      nextDraw = nextDraw ? nextDraw + 1000 / 60 : now + 1000 / 60
      if (now - nextDraw > 50) nextDraw = now + 1000 / 60
      const dt = previousTime ? Math.min(.05, (now - previousTime) / 1000) : 1 / 60
      previousTime = now
      pose.time += dt
      if (current !== stateRef.current) { current = stateRef.current; entered = 0 }
      entered += dt
      stepExpression(pose, current, dt, entered)
      try { draw() } catch { showFallback(); return }
      raf = requestAnimationFrame(frame)
    }
    const start = (): void => {
      stop()
      if (disposed || !ready || document.hidden || lost) return
      if (reducedMotion) {
        current = stateRef.current
        Object.assign(pose, expressionTarget(current, 0, 2), { paw: 0 })
        try { draw() } catch { showFallback() }
      } else raf = requestAnimationFrame(frame)
    }
    redrawRef.current = () => { if (reducedMotion) start() }
    const contextLost = (event: Event): void => { event.preventDefault(); lost = true; showFallback() }
    const restored = (): void => { lost = false; start() }
    let pending = images.length, failed = false
    for (const [index, image] of images.entries()) {
      image.onload = () => { if (!--pending && !disposed && !failed) { ready = true; start() } }
      image.onerror = () => { failed = true; showFallback() }
      image.src = Object.values(EXPRESSION_ASSETS)[index]
    }
    document.addEventListener('visibilitychange', start)
    canvas.addEventListener('contextlost', contextLost)
    canvas.addEventListener('contextrestored', restored)
    return () => {
      disposed = true
      stop()
      redrawRef.current = null
      document.removeEventListener('visibilitychange', start)
      canvas.removeEventListener('contextlost', contextLost)
      canvas.removeEventListener('contextrestored', restored)
      for (const image of images) { image.onload = null; image.onerror = null; image.src = '' }
      // A preference change reruns this effect before the new images load.
      // Keep one painted portrait visible while releasing the old surface.
      fallback.hidden = false
      canvas.hidden = true
      canvas.width = canvas.height = 1
    }
  }, [fallbackSrc, reducedMotion])
  return <><img ref={fallbackRef} className={className} src={fallbackSrc} alt="" draggable={false} aria-hidden="true" /><canvas ref={canvasRef} className={className} hidden aria-hidden="true" /></>
}
