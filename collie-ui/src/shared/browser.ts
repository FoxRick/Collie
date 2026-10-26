/** Data only. Python owns browser lifecycle, references and all action authority. */
export type BrowserOperation = 'open' | 'navigate' | 'observe' | 'fill' | 'click' | 'submit' | 'select' | 'check' | 'key' | 'scroll' | 'tab' | 'new_tab' | 'close_tab' | 'approve_destination' | 'resume' | 'screenshot' | 'handoff' | 'close' | 'list'
export interface BrowserCommand {
  operation: BrowserOperation
  session_id?: string
  url?: string
  ref?: string
  observation_id?: string
  tab_id?: string
  text?: string
  checked?: boolean
  direction?: 'up' | 'down'
  key?: 'Tab' | 'Shift+Tab' | 'ArrowDown' | 'ArrowUp' | 'ArrowLeft' | 'ArrowRight' | 'Escape'
}
export interface BrowserSession {
  session_id: string
  origin: string
  destinations: string[]
  task_id: string | null
  handed_off: boolean
}
export interface BrowserResult extends Partial<BrowserSession> {
  sessions?: BrowserSession[]
  observation_id?: string
  elements?: { ref: string; tag: string; type: string; role: string; label: string; options?: string[] }[]
  tabs?: { tab_id: string; origin: string; active: boolean }[]
  text?: string
  url?: string
  screenshot?: string
  blocked?: boolean
  rejected?: boolean
  message?: string
  closed?: boolean
  truncated?: boolean
  metrics?: { elapsed_ms: number; observation_bytes: number; blocked_requests: number }
}
