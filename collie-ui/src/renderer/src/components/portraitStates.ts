const concerned = new URL('../assets/portrait/concerned.webp', import.meta.url).href
const happy = new URL('../assets/portrait/happy.webp', import.meta.url).href
const idle = new URL('../assets/portrait/idle.webp', import.meta.url).href
const sleepy = new URL('../assets/portrait/sleepy.webp', import.meta.url).href
const thinking = new URL('../assets/portrait/thinking.webp', import.meta.url).href
const idleSheet = new URL('../assets/portrait/idle-sheet.webp', import.meta.url).href
const pointerLookSheet = new URL('../assets/portrait/pointer-look-sheet.png', import.meta.url).href
const waitingSheet = new URL('../assets/portrait/waiting-sheet.webp', import.meta.url).href
const clickReactionSheet = new URL('../assets/portrait/click-reaction-sheet.webp', import.meta.url).href
const deepWorkGlassesSheet = new URL(
  '../assets/portrait/deep-work-glasses-sheet.webp',
  import.meta.url
).href
const boneCompletionSheet = new URL(
  '../assets/portrait/bone-completion-sheet.webp',
  import.meta.url
).href

export const PORTRAIT_STATIC_FALLBACK = idle

/**
 * This manifest is intentionally limited to the composer portrait artwork.
 * Desktop-pet atlases and their supplemental strips must never be added here:
 * those images contain a full body and do not fit this face-only surface.
 */
export const FACE_ONLY_ASSET_MANIFEST = {
  kind: 'face-only' as const,
  idle: { src: idleSheet, columns: 3, rows: 2, frameCount: 6 },
  pointerLook: { src: pointerLookSheet, columns: 4, rows: 4, frameCount: 16 },
  waiting: { src: waitingSheet, columns: 3, rows: 2, frameCount: 6 },
  clickReaction: { src: clickReactionSheet, columns: 2, rows: 2, frameCount: 4 },
  deepWorkGlasses: { src: deepWorkGlassesSheet, columns: 3, rows: 2, frameCount: 6 },
  boneCompletion: { src: boneCompletionSheet, columns: 2, rows: 2, frameCount: 4 }
}

export interface FaceOnlyStrip {
  src: string
  columns: number
  rows: number
  frameCount: number
}

export interface PortraitFrame {
  src: string
  sourceIndex?: number
  columns?: number
  rows?: number
}

const still = (src: string): PortraitFrame => ({ src })
const stripFrames = (strip: FaceOnlyStrip): PortraitFrame[] =>
  Array.from({ length: strip.frameCount }, (_, sourceIndex) => ({
    src: strip.src,
    sourceIndex,
    columns: strip.columns,
    rows: strip.rows
  }))

export type PortraitState =
  | 'sleepy'
  | 'idle'
  | 'pointer_look'
  | 'working'
  | 'review'
  | 'waiting'
  | 'error'
  | 'completion'
  | 'click_reaction'
  | 'deep_work_glasses'

export const PORTRAIT_FRAME_SEQUENCE: Record<PortraitState, readonly PortraitFrame[]> = {
  // The closed-eye face is an existing genuine portrait pose, rather than a
  // CSS-scaled copy of idle. It gives the currently available face set a calm
  // blink until the dedicated six-frame idle strip is delivered.
  idle: [still(idle), still(idle), still(idle), still(sleepy), still(idle), still(idle)],
  sleepy: [still(sleepy)],
  pointer_look: [still(thinking), still(idle)],
  working: [still(thinking), still(idle), still(thinking)],
  review: [still(thinking), still(concerned), still(thinking)],
  waiting: [still(concerned), still(idle)],
  error: [still(concerned)],
  completion: [still(happy), still(happy), still(idle)],
  click_reaction: [still(happy), still(thinking), still(happy)],
  // This state is unreachable until a dedicated face-only glasses strip is
  // added to the manifest. Keeping a non-glasses fallback here preserves the
  // glasses-only invariant even if a caller selects it prematurely.
  deep_work_glasses: [still(thinking)]
}

export function portraitFramesFor(
  state: PortraitState,
  gazeDirection: number | null = null
): readonly PortraitFrame[] {
  if (state === 'idle' && FACE_ONLY_ASSET_MANIFEST.idle) {
    return stripFrames(FACE_ONLY_ASSET_MANIFEST.idle)
  }
  if (state === 'pointer_look' && FACE_ONLY_ASSET_MANIFEST.pointerLook) {
    const frames = stripFrames(FACE_ONLY_ASSET_MANIFEST.pointerLook)
    // A supplied 16-frame strip is indexed by the controller's clockwise
    // direction (0=up, 4=screen-right), rather than CSS-moving one face.
    return gazeDirection === null ? [frames[0]] : [frames[gazeDirection % frames.length]]
  }
  if (state === 'deep_work_glasses' && FACE_ONLY_ASSET_MANIFEST.deepWorkGlasses) {
    return stripFrames(FACE_ONLY_ASSET_MANIFEST.deepWorkGlasses)
  }
  if (state === 'waiting' && FACE_ONLY_ASSET_MANIFEST.waiting) {
    return stripFrames(FACE_ONLY_ASSET_MANIFEST.waiting)
  }
  if (state === 'click_reaction' && FACE_ONLY_ASSET_MANIFEST.clickReaction) {
    return stripFrames(FACE_ONLY_ASSET_MANIFEST.clickReaction)
  }
  if (state === 'completion' && FACE_ONLY_ASSET_MANIFEST.boneCompletion) {
    return stripFrames(FACE_ONLY_ASSET_MANIFEST.boneCompletion)
  }
  return PORTRAIT_FRAME_SEQUENCE[state]
}

export const PORTRAIT_FRAME_DURATION: Record<PortraitState, number> = {
  idle: 820,
  sleepy: 1600,
  pointer_look: 340,
  working: 520,
  review: 660,
  waiting: 760,
  error: 1200,
  completion: 430,
  click_reaction: 230,
  deep_work_glasses: 700
}

export const STATUS_COPY: Partial<Record<PortraitState, string[]>> = {
  working: [
    'Thinking through your task...',
    'Connecting the important details...',
    'Checking my reasoning before I answer...'
  ],
  review: [
    'Checking the current details...',
    'Following the strongest leads...'
  ],
  waiting: ['I need your input to continue.'],
  deep_work_glasses: ['Focusing on the details...']
}

const REVIEW_STATES = new Set(['searching', 'fetching', 'mapping', 'pantry', 'calendar', 'mail'])
const WAITING_STATES = new Set(['awaiting_approval', 'awaiting_input', 'needs_input', 'waiting'])
const WORKING_STATES = new Set([
  'startup',
  'planning',
  'processing',
  'recovering',
  'generating',
  'summarizing',
  'remembering'
])

export function portraitStateForEngine(state?: string): PortraitState | null {
  if (!state || state === 'idle' || state === 'done' || state === 'error') return null
  if (WAITING_STATES.has(state)) return 'waiting'
  if (REVIEW_STATES.has(state) || state === 'buddy') return 'review'
  if (WORKING_STATES.has(state)) return 'working'
  return 'working'
}

export function supportsFaceOnlyDeepWork(): boolean {
  return FACE_ONLY_ASSET_MANIFEST.deepWorkGlasses !== null
}

export function gazeEnabledForState(state: PortraitState): boolean {
  return !['sleepy', 'waiting', 'error', 'completion', 'click_reaction', 'deep_work_glasses'].includes(state)
}
