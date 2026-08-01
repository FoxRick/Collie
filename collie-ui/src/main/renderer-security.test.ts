import { readFileSync } from 'fs'
import { pathToFileURL } from 'url'
import { describe, expect, it, vi } from 'vitest'
import {
  guardIpcHandler,
  isTrustedIpcSender,
  isTrustedRendererUrl,
  shouldAllowAudioPermission
} from './renderer-security'

describe('renderer URL trust', () => {
  const devUrl = 'http://localhost:5173/'

  it('accepts only the exact development renderer URL', () => {
    expect(isTrustedRendererUrl('http://localhost:5173', devUrl)).toBe(true)
    expect(isTrustedRendererUrl('http://localhost.evil:5173/', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://localhost:51730/', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://localhost:5173/index.html', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://localhost:5173/?preview=1', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://localhost:5173/#other', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://localhost:5173@evil.test/', devUrl)).toBe(false)
    expect(isTrustedRendererUrl('http://user:password@localhost:5173/', devUrl)).toBe(false)
  })

  it('accepts only the exact packaged renderer file', () => {
    const rendererUrl = pathToFileURL('C:\\Collie\\resources\\app.asar\\out\\renderer\\index.html').href
    expect(isTrustedRendererUrl(rendererUrl, rendererUrl)).toBe(true)
    expect(isTrustedRendererUrl(rendererUrl.replace('index.html', 'settings.html'), rendererUrl)).toBe(false)
    expect(isTrustedRendererUrl(`${rendererUrl}?preview=1`, rendererUrl)).toBe(false)
    expect(isTrustedRendererUrl('file:///C:/Windows/System32/calc.exe', rendererUrl)).toBe(false)
  })
})

describe('IPC sender trust', () => {
  const trustedUrl = 'http://localhost:5173/'

  it('requires the trusted main frame and its trusted current URL', () => {
    const mainFrame = { url: trustedUrl }
    expect(isTrustedIpcSender(mainFrame, mainFrame, trustedUrl)).toBe(true)
    mainFrame.url = 'https://evil.test/'
    expect(isTrustedIpcSender(mainFrame, mainFrame, trustedUrl)).toBe(false)
    expect(isTrustedIpcSender({ url: trustedUrl }, mainFrame, trustedUrl)).toBe(false)
    expect(isTrustedIpcSender(null, mainFrame, trustedUrl)).toBe(false)
  })

  it('rejects guarded calls before invoking the handler', () => {
    const handler = vi.fn((value: string) => value.toUpperCase())
    const guarded = guardIpcHandler<{ trusted: boolean }, [string], string>(
      (event) => event.trusted,
      handler
    )
    expect(guarded({ trusted: true }, 'ok')).toBe('OK')
    expect(() => guarded({ trusted: false }, 'secret')).toThrow(/unexpected frame or renderer URL/)
    expect(handler).toHaveBeenCalledOnce()
  })

  it('registers every Collie IPC channel through the shared guard', () => {
    const source = readFileSync(new URL('./index.ts', import.meta.url), 'utf8')
    expect(source.match(/ipcMain\.handle\(/g)).toHaveLength(1)
    expect(source).not.toMatch(/ipcMain\.handle\(\s*['"]collie:/)
    expect(source.match(/\bhandle\(\s*['"]collie:/g)).toHaveLength(21)
  })
})

describe('media permission trust', () => {
  const trustedUrl = 'http://localhost:5173/'
  const mainWebContents = {}
  const trustedAudio = {
    isMainFrame: true,
    requestingUrl: trustedUrl,
    mediaTypes: ['audio'] as Array<'audio' | 'video'>
  }

  it('allows audio only for the trusted main window and frame', () => {
    expect(
      shouldAllowAudioPermission(mainWebContents, mainWebContents, 'media', trustedAudio, trustedUrl)
    ).toBe(true)
  })

  it.each([
    ['camera', mainWebContents, 'media', { ...trustedAudio, mediaTypes: ['video'] }],
    ['camera with audio', mainWebContents, 'media', { ...trustedAudio, mediaTypes: ['audio', 'video'] }],
    ['unspecified media', mainWebContents, 'media', { ...trustedAudio, mediaTypes: undefined }],
    ['subframe', mainWebContents, 'media', { ...trustedAudio, isMainFrame: false }],
    ['foreign URL', mainWebContents, 'media', { ...trustedAudio, requestingUrl: 'https://evil.test/' }],
    ['foreign window', {}, 'media', trustedAudio],
    ['other permission', mainWebContents, 'notifications', trustedAudio]
  ])('denies %s', (_name, webContents, permission, details) => {
    expect(
      shouldAllowAudioPermission(
        webContents,
        mainWebContents,
        permission,
        details as Parameters<typeof shouldAllowAudioPermission>[3],
        trustedUrl
      )
    ).toBe(false)
  })
})
