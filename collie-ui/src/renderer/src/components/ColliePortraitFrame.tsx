import { useEffect, useRef, useState } from 'react'
import type { PortraitState } from './portraitStates'
import { FACE_ONLY_ASSET_MANIFEST, PORTRAIT_STILLS } from './portraitStates'
import { chaseDirection, smoothFactor } from './colliePortraitMotion'

interface Props {
  state: PortraitState
  reducedMotion: boolean
  fallbackSrc: string
  className?: string
}

/**
 * Continuous portrait renderer for the Collie Circle.
 *
 * Replaces the old per-frame cell blitter with a single rAF loop that
 * reproduces the approved motion prototype: continuous blended gaze (no
 * 16-way snapping), crossfaded sheet walks with irregular blink cadence,
 * eased state transitions, breathing + micro-sway + head-tilt, and
 * per-sheet alpha-measured framing so the chest fur always reaches past the
 * circle's bottom edge.
 */
export default function ColliePortraitFrame({
  state,
  reducedMotion,
  fallbackSrc,
  className
}: Props): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null)
  const dprRef = useRef(1)
  const rafRef = useRef<number | null>(null)

  // ---- assets + per-sheet alpha framing ----
  const [ready, setReady] = useState(false)
  const assetsRef = useRef<Assets | null>(null)

  // ---- continuous motion state ----
  const stateRef = useRef<PortraitState>(state)
  const prevStateRef = useRef<PortraitState | null>(null)
  const stateT0Ref = useRef(0)
  const fromStateRef = useRef<PortraitState | null>(null)
  const fromT0Ref = useRef(0)
  const lastRef = useRef(0)
  const pxRef = useRef(0)
  const pyRef = useRef(0)
  const hasPointerRef = useRef(false)
  const gvXRef = useRef(0)
  const gvYRef = useRef(0)
  const contDirRef = useRef(0)
  const contDirInitRef = useRef(false)

  // Keep the loop reading the freshest state without re-renders.
  useEffect(() => {
    const now = performance.now()
    fromStateRef.current = prevStateRef.current
    fromT0Ref.current = now
    stateT0Ref.current = now
    prevStateRef.current = state
    stateRef.current = state
    if (state === 'pointer_look') contDirInitRef.current = false
  }, [state])

  // Window-wide pointer tracking (the dog follows the cursor anywhere over
  // the chat window, not just the ring).
  useEffect(() => {
    const onMove = (event: PointerEvent): void => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2
      const rad = Math.max(rect.width, rect.height) / 2
      let nx = (event.clientX - cx) / rad
      let ny = (event.clientY - cy) / rad
      const magnitude = Math.hypot(nx, ny)
      if (magnitude > 1.5) {
        nx *= 1.5 / magnitude
        ny *= 1.5 / magnitude
      }
      pxRef.current = nx
      pyRef.current = ny
      hasPointerRef.current = true
    }
    const onLeave = (): void => {
      hasPointerRef.current = false
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    document.addEventListener('pointerleave', onLeave)
    window.addEventListener('blur', onLeave)
    return () => {
      window.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerleave', onLeave)
      window.removeEventListener('blur', onLeave)
    }
  }, [])

  // Load sheets + stills, measuring each image's alpha bounding box so the
  // chest fur reaches past the circle's bottom edge (orange-gap fix).
  useEffect(() => {
    const sheets = {} as Partial<Record<SheetKey, SheetAsset>>
    const stills = {} as Partial<Record<StillKey, HTMLImageElement>>
    let pending = SHEET_KEYS.length + STILL_KEYS.length

    const done = (): void => {
      pending -= 1
      if (pending === 0) {
        assetsRef.current = { sheets, stills }
        setReady(true)
      }
    }

    for (const key of SHEET_KEYS) {
      const img = new Image()
      img.onload = () => {
        sheets[key] = {
          img,
          cols: SHEET_DEFS[key].cols,
          rows: SHEET_DEFS[key].rows,
          n: SHEET_DEFS[key].n,
          framing: measureFraming(img, SHEET_DEFS[key].cols, SHEET_DEFS[key].rows)
        }
        done()
      }
      img.onerror = () => done()
      img.src = SHEET_DEFS[key].src
    }
    for (const key of STILL_KEYS) {
      const img = new Image()
      img.onload = () => {
        stills[key] = img
        done()
      }
      img.onerror = () => done()
      img.src = PORTRAIT_STILLS[key]
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const context = canvas.getContext('2d')
    if (!context) return
    dprRef.current = window.devicePixelRatio || 1
    canvas.width = FRAME_SIZE * dprRef.current
    canvas.height = FRAME_SIZE * dprRef.current
    ctxRef.current = context

    if (reducedMotion) {
      drawStaticIdle()
      return
    }
    if (typeof requestAnimationFrame === 'undefined') return

    lastRef.current = performance.now()
    const frame = (now: number): void => {
      const dt = Math.min(0.05, (now - lastRef.current) / 1000)
      lastRef.current = now
      const t = now / 1000
      const lt = now - stateT0Ref.current
      const currentState = stateRef.current

      // Ambient look-around when the pointer is away.
      if (!hasPointerRef.current) {
        const wx = Math.sin(t * 0.31) * 0.42 + Math.sin(t * 0.13) * 0.18
        const wy = Math.cos(t * 0.23) * 0.22
        const f = 1 - Math.exp(-1.4 * dt)
        pxRef.current += (wx - pxRef.current) * f
        pyRef.current += (wy - pyRef.current) * f
      }

      // Gaze vector: responsive but lagging; damped while working/reviewing,
      // zeroed while sleepy or in deep-work glasses.
      let targetX = pxRef.current
      let targetY = pyRef.current
      if (currentState === 'working' || currentState === 'review') {
        targetX *= 0.25
        targetY *= 0.25
      }
      if (currentState === 'sleepy' || currentState === 'deep_work_glasses') {
        targetX = 0
        targetY = 0
      }
      const gazeFactor = smoothFactor(9, dt)
      gvXRef.current += (targetX - gvXRef.current) * gazeFactor
      gvYRef.current += (targetY - gvYRef.current) * gazeFactor

      // One continuously-smoothed look direction; blend two adjacent cells.
      if (currentState === 'pointer_look') {
        const degrees = (Math.atan2(gvXRef.current, -gvYRef.current) * 180) / Math.PI
        const target = ((degrees / 22.5) % LOOK_DIRECTION_COUNT + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
        if (!contDirInitRef.current) {
          contDirRef.current = target
          contDirInitRef.current = true
        }
        contDirRef.current = chaseDirection(contDirRef.current, target, 0.16)
      }

      const context2d = ctxRef.current
      if (!context2d) return
      context2d.setTransform(dprRef.current, 0, 0, dprRef.current, 0, 0)
      context2d.clearRect(0, 0, FRAME_SIZE, FRAME_SIZE)

      // Life at rest: breathing + layered micro-sway + micro head-tilt.
      const sleepy = currentState === 'sleepy'
      const breathe = Math.sin(t * (sleepy ? 1.1 : 1.9)) * (sleepy ? 0.01 : 0.006)
      const sway = currentState === 'deep_work_glasses' || currentState === 'working' ? 0.35 : 1
      const microX = (Math.sin(t * 0.7) * 1.2 + Math.sin(t * 1.31) * 0.6) * sway
      const microY = Math.cos(t * 0.61) * 0.8 * sway
      const scale =
        1 +
        breathe +
        (currentState === 'click_reaction'
          ? 0.02 * Math.sin(Math.min(1, lt / 900) * Math.PI)
          : 0)
      const rotation = gvXRef.current * 0.05 + Math.sin(t * 0.43) * 0.005 * sway
      const offsetX = gvXRef.current * 6 + microX
      const offsetY = gvYRef.current * 4.5 + microY + breathe * 8

      // Eased crossfade between states; the old state renders beneath.
      const sinceTransition = now - fromT0Ref.current
      let transitionWeight = 1
      if (fromStateRef.current !== null) {
        const w = Math.min(1, sinceTransition / STATE_FADE_MS)
        transitionWeight = w * w * (3 - 2 * w)
      }
      if (transitionWeight < 1 && fromStateRef.current !== null) {
        for (const layer of renderLayers(fromStateRef.current, now - fromT0Ref.current, contDirRef.current)) {
          drawLayer(layer, layer.alpha * (1 - transitionWeight), offsetX, offsetY, rotation, scale)
        }
      }
      for (const layer of renderLayers(currentState, lt, contDirRef.current)) {
        drawLayer(layer, layer.alpha * transitionWeight, offsetX, offsetY, rotation, scale)
      }
      if (transitionWeight >= 1) fromStateRef.current = null

      rafRef.current = requestAnimationFrame(frame)
    }
    rafRef.current = requestAnimationFrame(frame)
    return () => {
      if (rafRef.current !== null && typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(rafRef.current)
      }
      rafRef.current = null
    }
  }, [ready, reducedMotion])

  function drawStaticIdle(): void {
    const context = ctxRef.current
    if (!context) return
    context.setTransform(dprRef.current, 0, 0, dprRef.current, 0, 0)
    context.clearRect(0, 0, FRAME_SIZE, FRAME_SIZE)
    drawLayer({ kind: 'still', key: 'idle', alpha: 1 }, 1, 0, 0, 0, 1)
  }

  function drawLayer(
    layer: RenderLayer,
    alpha: number,
    dx: number,
    dy: number,
    rotation: number,
    scale: number
  ): void {
    const context = ctxRef.current
    const assets = assetsRef.current
    if (!context || !assets || alpha <= 0.003) return
    let img: HTMLImageElement
    let cols = 1
    let rows = 1
    let cell = 0
    let framing: Framing | null = null
    if (layer.kind === 'sheet') {
      const sheet = assets.sheets[layer.key]
      if (!sheet) return
      img = sheet.img
      cols = sheet.cols
      rows = sheet.rows
      cell = layer.cell
      framing = sheet.framing
    } else {
      const still = assets.stills[layer.key]
      if (!still) return
      img = still
    }
    if (!img.complete || !img.naturalWidth) return
    const sw = img.naturalWidth / cols
    const sh = img.naturalHeight / rows
    const sx = (cell % cols) * sw
    const sy = Math.floor(cell / cols) * sh
    const fit = FRAME_SIZE / Math.max(sw, sh)
    const dw = sw * fit
    const dh = sh * fit
    const scaleFactor = framing ? framing.s : 1
    const dyOffset = framing ? framing.dy : 0
    const width = dw * scaleFactor * scale
    const height = dh * scaleFactor * scale
    context.save()
    context.globalAlpha = Math.max(0, Math.min(1, alpha))
    context.translate(FRAME_SIZE / 2 + dx, FRAME_SIZE / 2 + dy + dyOffset)
    context.rotate(rotation)
    context.drawImage(img, sx, sy, sw, sh, -width / 2, -height / 2, width, height)
    context.restore()
  }

  return (
    <>
      <img className={className} src={fallbackSrc} alt="" draggable={false} aria-hidden="true" />
      <canvas ref={canvasRef} className={className} aria-hidden="true" />
    </>
  )
}

// ---------------------------------------------------------------------------
// Motion primitives ported from the approved prototype
// (collie-dog-motion-draft.html).
// ---------------------------------------------------------------------------

const FRAME_SIZE = 384
const FRAMING_OVERSHOOT = 10
const FRAMING_TOP_MARGIN = 14
const STATE_FADE_MS = 300
const LOOK_DIRECTION_COUNT = 16

type SheetKey = 'idle' | 'pointer' | 'waiting' | 'click' | 'bone' | 'glasses'
type StillKey = 'idle' | 'sleepy' | 'thinking' | 'concerned' | 'happy'

const SHEET_KEYS: SheetKey[] = ['idle', 'pointer', 'waiting', 'click', 'bone', 'glasses']
const STILL_KEYS: StillKey[] = ['idle', 'sleepy', 'thinking', 'concerned', 'happy']

const SHEET_DEFS: Record<SheetKey, { src: string; cols: number; rows: number; n: number }> = {
  idle: { src: FACE_ONLY_ASSET_MANIFEST.idle.src, cols: 3, rows: 2, n: 6 },
  pointer: { src: FACE_ONLY_ASSET_MANIFEST.pointerLook.src, cols: 4, rows: 4, n: 16 },
  waiting: { src: FACE_ONLY_ASSET_MANIFEST.waiting.src, cols: 3, rows: 2, n: 6 },
  click: { src: FACE_ONLY_ASSET_MANIFEST.clickReaction.src, cols: 2, rows: 2, n: 4 },
  bone: { src: FACE_ONLY_ASSET_MANIFEST.boneCompletion.src, cols: 2, rows: 2, n: 4 },
  glasses: { src: FACE_ONLY_ASSET_MANIFEST.deepWorkGlasses.src, cols: 3, rows: 2, n: 6 }
}

interface Framing {
  s: number
  dy: number
}

interface SheetAsset {
  img: HTMLImageElement
  cols: number
  rows: number
  n: number
  framing: Framing | null
}

interface Assets {
  sheets: Partial<Record<SheetKey, SheetAsset>>
  stills: Partial<Record<StillKey, HTMLImageElement>>
}

type RenderLayer =
  | { kind: 'sheet'; key: SheetKey; cell: number; alpha: number }
  | { kind: 'still'; key: StillKey; alpha: number }

const framingCache = new WeakMap<HTMLImageElement, Framing | null>()

/**
 * Measures the alpha bounding box across every cell of a sheet (downscaled to
 * 72px cells for speed) and returns the scale/offset that frames the fur from
 * (top + 14px margin) to (bottom + 10px overshoot past the circle edge).
 * Returns null when the image is not measurable; callers fall back to plain
 * contain-fit.
 */
function measureFraming(img: HTMLImageElement, cols: number, rows: number): Framing | null {
  const cached = framingCache.get(img)
  if (cached !== undefined) return cached
  const framing = computeFraming(img, cols, rows)
  framingCache.set(img, framing)
  return framing
}

function computeFraming(img: HTMLImageElement, cols: number, rows: number): Framing | null {
  if (!img.complete || !img.naturalWidth) return null
  const cellSize = 72
  const canvas = document.createElement('canvas')
  canvas.width = cellSize
  canvas.height = cellSize
  const g = canvas.getContext('2d', { willReadFrequently: true })
  if (!g) return null
  const sw = img.naturalWidth / cols
  const sh = img.naturalHeight / rows
  let top = cellSize
  let bottom = -1
  for (let i = 0; i < cols * rows; i++) {
    g.clearRect(0, 0, cellSize, cellSize)
    g.drawImage(img, (i % cols) * sw, Math.floor(i / cols) * sh, sw, sh, 0, 0, cellSize, cellSize)
    let data: Uint8ClampedArray
    try {
      data = g.getImageData(0, 0, cellSize, cellSize).data
    } catch {
      return null
    }
    for (let y = 0; y < cellSize; y++) {
      for (let x = 0; x < cellSize; x++) {
        if (data[(y * cellSize + x) * 4 + 3] > 16) {
          if (y < top) top = y
          if (y > bottom) bottom = y
        }
      }
    }
  }
  if (bottom < top) return null
  const topFraction = top / cellSize
  const bottomFraction = (bottom + 1) / cellSize
  const fit = FRAME_SIZE / Math.max(sw, sh)
  const drawHeight = sh * fit
  // Fur must span from (top edge + margin) to (bottom edge + overshoot).
  const spanNeeded = FRAME_SIZE - FRAMING_TOP_MARGIN + FRAMING_OVERSHOOT
  const scale = Math.max(1, spanNeeded / ((bottomFraction - topFraction) * drawHeight))
  const dy = FRAME_SIZE / 2 + FRAMING_OVERSHOOT - (bottomFraction - 0.5) * drawHeight * scale
  return { s: scale, dy }
}

// Deterministic per-cycle hold variation — organic blink cadence.
const hash01 = (i: number): number => {
  const s = Math.sin(i * 127.1 + 311.7) * 43758.5453
  return s - Math.floor(s)
}

/** Walks a sheet's cells, crossfading between neighbours with irregular holds. */
function seqPlayer(n: number, holdBase: number, fadeMs: number): (lt: number) => Array<[number, number]> {
  return (lt) => {
    let acc = 0
    let step = 0
    for (; step < 256; step++) {
      const hold = holdBase * (0.82 + 0.36 * hash01(step))
      if (lt < acc + hold + fadeMs) break
      acc += hold + fadeMs
    }
    const hold = holdBase * (0.82 + 0.36 * hash01(step))
    const into = lt - acc
    const w = Math.max(0, Math.min(1, (into - hold) / fadeMs))
    const eased = w * w * (3 - 2 * w)
    return [
      [step % n, 1 - eased],
      [(step + 1) % n, eased]
    ]
  }
}

/** Alternates two stills with eased crossfades. */
function pulsePlayer(holdMs: number, fadeMs: number): (lt: number) => Array<[0 | 1, number]> {
  return (lt) => {
    const total = holdMs + fadeMs
    const phase = Math.floor(lt / total) % 2
    const into = lt % total
    const w = Math.max(0, Math.min(1, (into - holdMs) / fadeMs))
    const eased = w * w * (3 - 2 * w)
    return phase === 0
      ? [
          [0, 1 - eased],
          [1, eased]
        ]
      : [
          [1, 1 - eased],
          [0, eased]
        ]
  }
}

const idleSeq = seqPlayer(SHEET_DEFS.idle.n, 700, 170)
const waitingSeq = seqPlayer(SHEET_DEFS.waiting.n, 650, 200)
const boneSeq = seqPlayer(SHEET_DEFS.bone.n, 320, 130)
const clickSeq = seqPlayer(SHEET_DEFS.click.n, 170, 90)
const glassesSeq = seqPlayer(SHEET_DEFS.glasses.n, 600, 180)
const workPulse = pulsePlayer(640, 260)
const reviewPulse = pulsePlayer(580, 260)

function renderLayers(state: PortraitState, lt: number, contDir: number): RenderLayer[] {
  switch (state) {
    case 'idle':
      return idleSeq(lt).map(([cell, alpha]) => ({ kind: 'sheet', key: 'idle', cell, alpha }))
    case 'pointer_look': {
      const index = Math.floor(contDir)
      const frac = contDir - index
      const eased = frac * frac * (3 - 2 * frac)
      const first = ((index % LOOK_DIRECTION_COUNT) + LOOK_DIRECTION_COUNT) % LOOK_DIRECTION_COUNT
      return [
        { kind: 'sheet', key: 'pointer', cell: first, alpha: 1 - eased },
        { kind: 'sheet', key: 'pointer', cell: (first + 1) % LOOK_DIRECTION_COUNT, alpha: eased }
      ]
    }
    case 'working':
      return workPulse(lt).map(([slot, alpha]) => ({
        kind: 'still',
        key: slot === 0 ? 'thinking' : 'idle',
        alpha
      }))
    case 'review':
      return reviewPulse(lt).map(([slot, alpha]) => ({
        kind: 'still',
        key: slot === 0 ? 'thinking' : 'concerned',
        alpha
      }))
    case 'waiting':
      return waitingSeq(lt).map(([cell, alpha]) => ({ kind: 'sheet', key: 'waiting', cell, alpha }))
    case 'sleepy':
      return [{ kind: 'still', key: 'sleepy', alpha: 1 }]
    case 'error':
      return [{ kind: 'still', key: 'concerned', alpha: 1 }]
    case 'completion':
      return boneSeq(lt).map(([cell, alpha]) => ({ kind: 'sheet', key: 'bone', cell, alpha }))
    case 'click_reaction':
      return clickSeq(lt).map(([cell, alpha]) => ({ kind: 'sheet', key: 'click', cell, alpha }))
    case 'deep_work_glasses':
      return glassesSeq(lt).map(([cell, alpha]) => ({ kind: 'sheet', key: 'glasses', cell, alpha }))
  }
}
