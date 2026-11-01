export type DesktopAction =
  | { type: 'new-chat' }
  | { type: 'open-chat'; conversationId: string }
  | { type: 'settings' }

export interface DesktopMenuState {
  conversations: { id: string; title: string }[]
  activeId: string | null
}
