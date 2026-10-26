export const PORTRAIT_STATIC_FALLBACK = new URL('../assets/portrait/expression-ready-v2.webp', import.meta.url).href

export type PortraitState =
  | 'attentive'
  | 'thinking'
  | 'writing'
  | 'delegating'
  | 'listening'
  | 'sleepy'
  | 'idle'
  | 'working'
  | 'review'
  | 'waiting'
  | 'error'
  | 'completion'
  | 'click_reaction'
  | 'happy_reaction'

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
  waiting: ['I need your input to continue.']
}

const REVIEW_STATES = new Set(['searching', 'fetching', 'mapping', 'pantry', 'calendar', 'mail'])
const WAITING_STATES = new Set(['awaiting_approval', 'awaiting_input', 'needs_input', 'waiting'])

export function portraitStateForEngine(state?: string): PortraitState | null {
  if (!state || state === 'idle' || state === 'done' || state === 'error') return null
  if (['planning', 'remembering', 'thinking'].includes(state)) return 'thinking'
  if (['generating', 'summarizing', 'writing'].includes(state)) return 'writing'
  if (state === 'buddy' || state === 'delegating') return 'delegating'
  if (WAITING_STATES.has(state)) return 'waiting'
  if (REVIEW_STATES.has(state)) return 'review'
  return 'working'
}
