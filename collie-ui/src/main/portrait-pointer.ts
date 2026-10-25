import type { BrowserWindow, Screen } from 'electron'

/** Read only the pointer's position relative to Collie's content area. No
 * hooks, click/key capture, screen capture, history, or background polling. */
export function portraitPointer(
  window: BrowserWindow | null,
  screen: Pick<Screen, 'getCursorScreenPoint'>,
  environment: { platform: string; sessionType?: string; waylandDisplay?: string; ozone?: string }
): { x: number; y: number } | null {
  if (!window || window.isDestroyed() || !window.isVisible() || window.isMinimized()) return null
  const wayland = environment.ozone === 'wayland' ||
    (environment.ozone !== 'x11' && (environment.sessionType === 'wayland' || !!environment.waylandDisplay))
  if (environment.platform === 'linux' && wayland) return null
  const point = screen.getCursorScreenPoint()
  const bounds = window.getContentBounds()
  const zoom = window.webContents.getZoomFactor()
  return { x: (point.x - bounds.x) / zoom, y: (point.y - bounds.y) / zoom }
}
