import { describe, expect, it, vi } from 'vitest'

const image = vi.hoisted(() => ({ setTemplateImage: vi.fn(), resize: vi.fn() }))
const nativeImage = vi.hoisted(() => ({ createFromNamedImage: vi.fn(), createFromPath: vi.fn() }))
vi.mock('electron', () => ({ nativeImage }))
import { createTrayImage, shouldHideOnClose, shouldQuitWithoutWindows } from './tray-platform'

describe('platform tray lifecycle', () => {
  it.each([
    ['win32', true, true, false], ['win32', false, false, true],
    ['darwin', true, true, false], ['darwin', false, false, false],
    ['linux', true, false, true], ['linux', false, false, true]
  ] as const)('%s with tray=%s chooses hide=%s and quit=%s', (platform, tray, hide, quit) => {
    expect(shouldHideOnClose(platform, tray)).toBe(hide)
    expect(shouldQuitWithoutWindows(platform, tray)).toBe(quit)
  })

  it('uses a template system image on macOS', () => {
    nativeImage.createFromNamedImage.mockReturnValue(image)
    expect(createTrayImage('/icon.png', 'darwin')).toBe(image)
    expect(nativeImage.createFromNamedImage).toHaveBeenCalledWith('pawprint.fill', { pointSize: 16 })
    expect(image.setTemplateImage).toHaveBeenCalledWith(true)
  })

  it.each(['win32', 'linux'] as const)('keeps the branded icon on %s', (platform) => {
    nativeImage.createFromPath.mockReturnValue(image)
    image.resize.mockReturnValue(image)
    expect(createTrayImage('/icon.png', platform)).toBe(image)
    expect(nativeImage.createFromPath).toHaveBeenCalledWith('/icon.png')
    expect(image.resize).toHaveBeenCalledWith({ width: 24, height: 24 })
  })
})
