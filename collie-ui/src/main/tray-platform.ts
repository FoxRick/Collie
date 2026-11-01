import { nativeImage, type NativeImage } from 'electron'

export function createTrayImage(path: string, platform: NodeJS.Platform = process.platform): NativeImage {
  if (platform === 'darwin') {
    // A system template remains legible in light/dark menu bars and on Retina.
    const image = nativeImage.createFromNamedImage('pawprint.fill', { pointSize: 16 })
    image.setTemplateImage(true)
    return image
  }
  return nativeImage.createFromPath(path).resize({ width: 24, height: 24 })
}

export function shouldHideOnClose(platform: NodeJS.Platform, trayAvailable: boolean): boolean {
  // Linux can construct a Tray even when the desktop has no visible tray host.
  // Keep closing predictable there; minimize leaves the app running in its taskbar.
  return platform !== 'linux' && trayAvailable
}

export function shouldQuitWithoutWindows(platform: NodeJS.Platform, trayAvailable: boolean): boolean {
  return platform === 'linux' || (platform !== 'darwin' && !trayAvailable)
}
