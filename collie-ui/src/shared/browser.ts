/** Browser IPC carries data only; Python resolves authority and owns lifecycle. */
export type BrowserOperation = 'open' | 'navigate' | 'observe' | 'fill' | 'click' | 'screenshot' | 'handoff' | 'close' | 'list'

export interface BrowserCommand {
  operation: BrowserOperation
  session_id?: string
  url?: string
  selector?: string
  text?: string
}

export interface BrowserSession {
  session_id: string
  origin: string
  task_id: string | null
  handed_off: boolean
}

export interface BrowserResult extends Partial<BrowserSession> {
  sessions?: BrowserSession[]
  elements?: { selector: string; tag: string; label: string }[]
  text?: string
  url?: string
  screenshot?: string
  blocked?: boolean
  message?: string
  closed?: boolean
}
