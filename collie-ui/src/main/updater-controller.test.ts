import { EventEmitter } from 'events'
import { describe, expect, it, vi } from 'vitest'
import {
  ActiveWorkTracker,
  UpdateController,
  type AutoUpdaterLike
} from './updater-controller'

class FakeUpdater extends EventEmitter implements AutoUpdaterLike {
  autoDownload = true
  autoInstallOnAppQuit = true
  allowPrerelease = false
  channel: string | null = null
  checkForUpdates = vi.fn(async () => undefined)
  downloadUpdate = vi.fn(async () => undefined)
  quitAndInstall = vi.fn()
}

describe('UpdateController', () => {
  it('configures an explicit alpha update lifecycle', async () => {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    const controller = new UpdateController(updater, '0.1.0-alpha.1', true, tracker)

    expect(updater.autoDownload).toBe(false)
    expect(updater.autoInstallOnAppQuit).toBe(false)
    expect(updater.allowPrerelease).toBe(true)
    expect(updater.channel).toBe('alpha')

    await controller.check()
    updater.emit('update-available', { version: '0.1.0-alpha.2' })
    expect(controller.getStatus()).toMatchObject({
      phase: 'available',
      availableVersion: '0.1.0-alpha.2'
    })

    await controller.download()
    updater.emit('download-progress', { percent: 42.5 })
    updater.emit('update-downloaded', { version: '0.1.0-alpha.2' })
    expect(updater.downloadUpdate).toHaveBeenCalledOnce()
    expect(controller.getStatus()).toMatchObject({ phase: 'ready', percent: 100 })
  })

  it('reports no update as current and sanitizes bounded errors', () => {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    const controller = new UpdateController(updater, '0.1.0-alpha.1', true, tracker)

    updater.emit('update-not-available', { version: '0.1.0-alpha.1' })
    expect(controller.getStatus()).toMatchObject({
      phase: 'current',
      message: 'Collie is up to date.'
    })

    updater.emit(
      'error',
      new Error(`Request failed at https://updates.example.test/feed?token=supersecret
        C:\\Users\\person\\private\\release.yml`)
    )
    const status = controller.getStatus()
    expect(status.phase).toBe('failed')
    expect(status.message).not.toContain('supersecret')
    expect(status.message).not.toContain('person')
    expect(status.message!.length).toBeLessThanOrEqual(240)
  })

  it('refuses install while any protected work is active', () => {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    const controller = new UpdateController(updater, '0.1.0-alpha.1', true, tracker)
    updater.emit('update-downloaded', { version: '0.1.0-alpha.2' })

    expect(controller.restartAndInstall().blockedBy).toEqual(['activity status unavailable'])

    tracker.update({ chats: 1, approvals: 1, routines: 1, externalActions: 1 })
    expect(controller.restartAndInstall().blockedBy).toEqual([
      'active chat',
      'pending approval',
      'running routine',
      'active external action'
    ])
    expect(updater.quitAndInstall).not.toHaveBeenCalled()
  })

  it('installs only after a ready update and an idle snapshot', () => {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    const beforeInstall = vi.fn()
    const controller = new UpdateController(
      updater,
      '0.1.0-alpha.1',
      true,
      tracker,
      beforeInstall
    )
    tracker.update({ chats: 0, approvals: 0, routines: 0, externalActions: 0 })

    expect(controller.restartAndInstall().blockedBy).toEqual(['update not ready'])
    updater.emit('update-downloaded', { version: '0.1.0-alpha.2' })
    expect(controller.restartAndInstall()).toEqual({ installed: true, blockedBy: [] })
    expect(beforeInstall).toHaveBeenCalledOnce()
    expect(updater.quitAndInstall).toHaveBeenCalledWith(false, true)
  })
})
