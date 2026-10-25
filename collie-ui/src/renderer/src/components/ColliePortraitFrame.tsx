import { useEffect, useRef } from 'react'
import { smoothFactor } from './colliePortraitMotion'
import { prepareFaceArtwork, type FaceArtwork } from './colliePortraitFace'
import { FACE_ONLY_ASSET_MANIFEST, type PortraitState } from './portraitStates'
import { attentionTarget, drawRig, rigPoint } from './colliePortraitRig'
interface Props {
  state: PortraitState
  reducedMotion: boolean
  fallbackSrc: string
  className?: string
}
const ease = (v: number): number => { const t = Math.max(0, Math.min(1, v)); return t * t * (3 - 2 * t); }
export default function ColliePortraitFrame({ state, reducedMotion, fallbackSrc, className }: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null), fallbackRef = useRef<HTMLImageElement>(null), stateRef = useRef(state)
  const poseRef = useRef({ x: 0, y: 0, time: 0, mouth: 0, wave: 0, glasses: 0 })
  useEffect(() => { stateRef.current = state; }, [state])
  useEffect(() => {
    const canvas = canvasRef.current, fallback = fallbackRef.current
    if (!canvas || !fallback)
      return
    const ctx = canvas.getContext('2d')
    if (!ctx)
      return
    let disposed = false, lost = false, art: FaceArtwork | null = null, raf: number | null = null, last = 0, nextDraw = 0, reaction = 0, wasReacting = false
    let target = { x: 0, y: 0 }
    const pose = poseRef.current
    const stop = (): void => {
      if (raf !== null) cancelAnimationFrame(raf)
      raf = null
      last = 0
      nextDraw = 0
    }
    const draw = (): void => {
      if (!art)
        return
      const side = Math.max(1, Math.round(Math.min(2, window.devicePixelRatio || 1) * (canvas.getBoundingClientRect().width || 136)))
      if (canvas.width !== side || canvas.height !== side) {
        canvas.width = side
        canvas.height = side
      }
      ctx.setTransform(side / 384, 0, 0, side / 384, 0, 0)
      ctx.clearRect(0, 0, 384, 384)
      drawRig(ctx, art.neutral.texture, pose)
      if (pose.mouth > .001)
        drawRig(ctx, art.mouth.texture, pose, ease(pose.mouth))
      const blink = reducedMotion ? 0 : stateRef.current === 'sleepy' ? 1 : Math.max(0, 1 - Math.abs(pose.time % 5.1 - 4.7) / .13)
      if (blink > 0)
        drawRig(ctx, art.blink.texture, pose, blink)
      const landmarks = art.neutral.features.map(p => rigPoint(p.x, p.y, pose))
      canvas.dataset.blink = blink.toFixed(3)
      if (pose.glasses > .001) {
        const left = landmarks[3], right = landmarks[4]
        ctx.save()
        ctx.globalAlpha = pose.glasses
        ctx.lineWidth = 5
        ctx.strokeStyle = '#211e21'
        ctx.fillStyle = 'rgba(116,161,181,.12)'
        for (const eye of [left, right]) {
          ctx.beginPath()
          ctx.ellipse(eye.x, eye.y, 24, 20, .05, 0, Math.PI * 2)
          ctx.fill()
          ctx.stroke()
        }
        ctx.beginPath()
        ctx.moveTo(left.x + 24, left.y)
        ctx.lineTo(right.x - 24, right.y)
        ctx.stroke()
        ctx.restore()
      }
      if (pose.wave > .001) {
        // Original raised FORELEG pivots from the chest; no floating fist.
        ctx.save()
        ctx.translate(80, 384)
        ctx.rotate((1 - pose.wave) * -.65 + Math.sin(reaction * 15) * .035 * pose.wave)
        ctx.translate(-80, -384 + (1 - pose.wave) * 150)
        ctx.drawImage(art.arm, 0, 0)
        ctx.restore()
      }
      canvas.dataset.noseX = landmarks[5].x.toFixed(2)
      canvas.dataset.noseY = landmarks[5].y.toFixed(2)
      canvas.dataset.eyeX = landmarks[3].x.toFixed(2)
      canvas.dataset.eyeY = landmarks[3].y.toFixed(2)
      canvas.dataset.mouth = pose.mouth.toFixed(3)
      canvas.dataset.wave = pose.wave.toFixed(3)
      fallback.hidden = true
      canvas.hidden = false
    }
    const frame = (now: number): void => {
      raf = null
      if (disposed || document.hidden || lost || !art)
        return
      if (now < nextDraw - .5) {
        raf = requestAnimationFrame(frame)
        return
      }
      nextDraw = nextDraw ? nextDraw + 1000 / 60 : now + 1000 / 60
      if (now - nextDraw > 50)
        nextDraw = now + 1000 / 60
      const dt = last ? Math.min(.05, (now - last) / 1000) : 1 / 60
      last = now
      pose.time += dt
      const current = stateRef.current, reacting = ['click_reaction', 'happy_reaction', 'completion'].includes(current)
      if (reacting && !wasReacting)
        reaction = 0
      wasReacting = reacting
      if (reacting)
        reaction += dt
      const busy = ['working', 'review', 'deep_work_glasses'].includes(current), attention = current === 'sleepy' ? 0 : busy ? .4 : 1, f = smoothFactor(8, dt)
      pose.x += (target.x * attention - pose.x) * f
      pose.y += (target.y * attention - pose.y) * f
      const wave = reacting ? ease((reaction - .12) / .24) * (1 - ease((reaction - .85) / .4)) : 0
      pose.wave += (wave - pose.wave) * smoothFactor(18, dt)
      const smile = reacting && reaction < 1.18 ? (current === 'happy_reaction' || current === 'completion' ? 1 : .35) : 0
      pose.mouth += (smile - pose.mouth) * smoothFactor(11, dt)
      pose.glasses += ((current === 'deep_work_glasses' ? 1 : 0) - pose.glasses) * f
      try {
        draw()
      }
      catch {
        fallback.hidden = false
        canvas.hidden = true
        return
      }
      if (!reducedMotion)
        raf = requestAnimationFrame(frame)
    }
    const start = (): void => { stop(); if (!disposed && art && !document.hidden && !lost) {
      if (reducedMotion) {
        pose.x = 0
        pose.y = 0
        pose.mouth = 0
        pose.wave = 0
        draw()
      }
      else
        raf = requestAnimationFrame(frame)
    } }
    const move = (event: PointerEvent): void => { const r = canvas.getBoundingClientRect(), radius = Math.max(1, r.width / 2); target = attentionTarget((event.clientX - r.left - radius) / radius, (event.clientY - r.top - r.height / 2) / radius); }
    const leave = (): void => { target = { x: 0, y: 0 }; }
    const contextLost = (event: Event): void => { event.preventDefault(); lost = true; stop(); fallback.hidden = false; canvas.hidden = true; }
    const restored = (): void => {
      lost = false
      try {
        // Source canvases can lose their backing stores along with the main
        // context. Rebuild layers from the retained original image elements.
        art = prepareFaceArtwork(images[0], images[1], images[2])
        start()
      } catch {
        fallback.hidden = false
        canvas.hidden = true
      }
    }
    const images = [new Image(), new Image(), new Image()]
    let pending = 3, failed = false
    const loaded = (): void => { if (--pending || disposed || failed)
      return; try {
      art = prepareFaceArtwork(images[0], images[1], images[2])
      start()
    }
    catch {
      fallback.hidden = false
      canvas.hidden = true
    } }
    for (const image of images) {
      image.onload = loaded
      image.onerror = () => { failed = true; stop(); fallback.hidden = false; canvas.hidden = true; }
    }
    images[0].src = FACE_ONLY_ASSET_MANIFEST.pointerLook.src
    images[1].src = FACE_ONLY_ASSET_MANIFEST.clickReaction.src
    images[2].src = FACE_ONLY_ASSET_MANIFEST.idle.src
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('blur', leave)
    document.addEventListener('pointerleave', leave)
    document.addEventListener('visibilitychange', start)
    canvas.addEventListener('contextlost', contextLost)
    canvas.addEventListener('contextrestored', restored)
    return () => { disposed = true; stop(); window.removeEventListener('pointermove', move); window.removeEventListener('blur', leave); document.removeEventListener('pointerleave', leave); document.removeEventListener('visibilitychange', start); canvas.removeEventListener('contextlost', contextLost); canvas.removeEventListener('contextrestored', restored); for (const image of images) {
      image.onload = null
      image.onerror = null
    } }
  }, [fallbackSrc, reducedMotion])
  return <><img ref={fallbackRef} className={className} src={fallbackSrc} alt="" draggable={false} aria-hidden="true"/><canvas ref={canvasRef} className={className} hidden aria-hidden="true"/></>
}
