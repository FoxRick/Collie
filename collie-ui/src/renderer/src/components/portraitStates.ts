const concerned = new URL('../assets/portrait/concerned.webp', import.meta.url).href
const happy = new URL('../assets/portrait/happy.webp', import.meta.url).href
const idle = new URL('../assets/portrait/idle.webp', import.meta.url).href
const sleepy = new URL('../assets/portrait/sleepy.webp', import.meta.url).href
const thinking = new URL('../assets/portrait/thinking.webp', import.meta.url).href

export type PortraitState =
  | 'sleepy'
  | 'idle'
  | 'attentive'
  | 'thinking'
  | 'searching'
  | 'writing'
  | 'listening'
  | 'happy'
  | 'celebrate'
  | 'bark'
  | 'concerned'
  | 'paw_over_ring'

export const PORTRAIT_ASSET: Record<PortraitState, string> = {
  sleepy,
  idle,
  attentive: thinking,
  thinking,
  searching: thinking,
  writing: happy,
  listening: thinking,
  happy,
  celebrate: happy,
  bark: happy,
  concerned,
  paw_over_ring: happy
}

export const STATUS_COPY: Partial<Record<PortraitState, string[]>> = {
  thinking: [
    'Thinking through your task…',
    'Connecting the important details…',
    'Checking my reasoning before I answer…'
  ],
  searching: [
    'Checking the current details…',
    'Fetching the most useful information…',
    'Following the strongest leads…'
  ],
  writing: [
    'Writing a clear answer for you…',
    'Putting the final response together…',
    'Polishing the details before I send…'
  ],
  listening: ['A specialist is helping with this…']
}

const SEARCH_STATES = new Set(['searching', 'fetching', 'mapping', 'pantry', 'calendar', 'mail'])
const WRITING_STATES = new Set(['generating', 'summarizing', 'remembering'])
const THINKING_STATES = new Set(['startup', 'planning', 'processing', 'recovering'])

export function portraitStateForEngine(state?: string): PortraitState | null {
  if (!state || state === 'idle' || state === 'done' || state === 'error') return null
  if (SEARCH_STATES.has(state)) return 'searching'
  if (WRITING_STATES.has(state)) return 'writing'
  if (THINKING_STATES.has(state)) return 'thinking'
  if (state === 'buddy') return 'listening'
  return 'thinking'
}

export function gazeEnabledForState(state: PortraitState): boolean {
  return !['sleepy', 'searching', 'bark', 'concerned'].includes(state)
}
