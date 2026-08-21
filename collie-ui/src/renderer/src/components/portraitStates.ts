const concerned = new URL('../assets/portrait/concerned.webp', import.meta.url).href
const happy = new URL('../assets/portrait/happy.webp', import.meta.url).href
const idle = new URL('../assets/portrait/idle.webp', import.meta.url).href
const sleepy = new URL('../assets/portrait/sleepy.webp', import.meta.url).href
const thinking = new URL('../assets/portrait/thinking.webp', import.meta.url).href
const idleSheet = new URL('../assets/portrait/idle-sheet.webp', import.meta.url).href
const pointerLookSheet = new URL('../assets/portrait/pointer-look-sheet.webp', import.meta.url).href
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

export const PORTRAIT_STILLS = { idle, sleepy, thinking, concerned, happy } as const

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
