import type { MenuItemConstructorOptions } from 'electron'
import type { DesktopAction, DesktopMenuState } from '../shared/desktop-menu'

export function desktopMenu(
  state: DesktopMenuState,
  open: () => void,
  dispatch: (action: DesktopAction) => void,
  quit: () => void,
  platform: NodeJS.Platform = process.platform
): MenuItemConstructorOptions[] {
  return [
    { label: 'Open Collie', click: open },
    { label: 'New chat', click: () => dispatch({ type: 'new-chat' }) },
    {
      label: 'Recent chats',
      submenu: state.conversations.length
        ? state.conversations.map(({ id, title }) => ({
            // Escape menu accelerators outside macOS in user-supplied titles.
            label: platform === 'darwin'
              ? (title.trim() || 'New chat').slice(0, 80)
              : (title.trim() || 'New chat').slice(0, 80).replace(/&/g, '&&'),
            type: 'checkbox' as const,
            checked: id === state.activeId,
            click: (item) => {
              // Native checkboxes toggle before click. Selection belongs to the chat shell.
              item.checked = id === state.activeId
              dispatch({ type: 'open-chat', conversationId: id })
            }
          }))
        : [{ label: 'No recent chats', enabled: false }]
    },
    { type: 'separator' },
    { label: 'Settings', click: () => dispatch({ type: 'settings' }) },
    { type: 'separator' },
    { label: platform === 'win32' ? 'Exit Collie' : 'Quit Collie', click: quit }
  ]
}

/** Keep the latest requested destination until the chat shell has mounted. */
export class DesktopActionQueue {
  private ready = false
  private pending: DesktopAction | null = null

  constructor(private send: (action: DesktopAction) => void) {}

  dispatch(action: DesktopAction): void {
    if (this.ready) this.send(action)
    else this.pending = action
  }

  setReady(ready: boolean): void {
    this.ready = ready
    if (ready && this.pending) {
      const action = this.pending
      this.pending = null
      this.send(action)
    }
  }
}
