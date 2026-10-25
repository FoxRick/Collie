import type { BrowserWindow } from 'electron'
import { describe, expect, it, vi } from 'vitest'
import { portraitPointer } from './portrait-pointer'

const windowStub = (overrides = {}): BrowserWindow => ({
  isDestroyed: () => false,
  isVisible: () => true,
  isMinimized: () => false,
  getContentBounds: () => ({ x: -1200, y: 200, width: 1000, height: 700 }),
  webContents: { getZoomFactor: () => 1.25 },
  ...overrides
}) as unknown as BrowserWindow

describe('portrait desktop pointer', () => {
  it('returns CSS coordinates outside an unfocused window, including negative-origin displays', () => {
    const screen = { getCursorScreenPoint: () => ({ x: 100, y: 100 }) }
    expect(portraitPointer(windowStub(), screen, { platform: 'win32' })).toEqual({ x: 1040, y: -80 })
  })
  it('does not read a desktop pointer for hidden/minimized windows or Wayland', () => {
    const screen = { getCursorScreenPoint: vi.fn() }
    expect(portraitPointer(windowStub({ isVisible: () => false }), screen, { platform: 'win32' })).toBeNull()
    expect(portraitPointer(windowStub({ isMinimized: () => true }), screen, { platform: 'win32' })).toBeNull()
    expect(portraitPointer(windowStub(), screen, { platform: 'linux', waylandDisplay: 'wayland-0' })).toBeNull()
    expect(screen.getCursorScreenPoint).not.toHaveBeenCalled()
  })
  it('permits explicit X11 under a Wayland session', () => {
    const screen = { getCursorScreenPoint: () => ({ x: -1200, y: 200 }) }
    expect(portraitPointer(windowStub(), screen, { platform: 'linux', sessionType: 'wayland', ozone: 'x11' })).toEqual({ x: 0, y: 0 })
  })
})
