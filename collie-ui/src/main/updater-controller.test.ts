import { EventEmitter } from 'events'
import { mkdtempSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ActiveWorkTracker,
  UpdateController,
  type AutoUpdaterLike
} from './updater-controller'
import { readUpdateBootRecord, writeUpdateBootRecord } from './update-boot-record'

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

describe('UpdateController boot verification', () => {
  let dir: string
  let bootRecordPath: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'collie-update-controller-'))
    bootRecordPath = join(dir, 'update-boot-record.json')
  })

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  function controllerFor(version: string): UpdateController {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    tracker.update({ chats: 0, approvals: 0, routines: 0, externalActions: 0 })
    return new UpdateController(updater, version, true, tracker, undefined, bootRecordPath)
  }

  it('records the pending install before quitting to install', () => {
    const updater = new FakeUpdater()
    const tracker = new ActiveWorkTracker()
    tracker.update({ chats: 0, approvals: 0, routines: 0, externalActions: 0 })
    const controller = new UpdateController(
      updater,
      '0.1.0-alpha.4',
      true,
      tracker,
      undefined,
      bootRecordPath
    )
    updater.emit('update-downloaded', { version: '0.1.0-alpha.5' })

    expect(controller.restartAndInstall().installed).toBe(true)
    expect(readUpdateBootRecord(bootRecordPath)).toMatchObject({
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4'
    })
  })

  it('accepts the update when the pending version boots healthy', () => {
    const controller = controllerFor('0.1.0-alpha.5')
    writeUpdateBootRecord(bootRecordPath, {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    })

    controller.evaluateBoot(true)
    expect(controller.getStatus().phase).toBe('current')
    expect(readUpdateBootRecord(bootRecordPath)).toMatchObject({
      pendingVersion: null,
      previousVersion: null,
      lastGoodVersion: '0.1.0-alpha.5'
    })
  })

  it('surfaces a failed-update notice when the new version cannot start its core', () => {
    const controller = controllerFor('0.1.0-alpha.5')
    writeUpdateBootRecord(bootRecordPath, {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    })

    controller.evaluateBoot(false)
    const status = controller.getStatus()
    expect(status.phase).toBe('rollback')
    expect(status.message).toContain('did not start properly')
    expect(status.failedUpdate).toEqual({
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4'
    })
    expect(readUpdateBootRecord(bootRecordPath)?.pendingVersion).toBe('0.1.0-alpha.5')
  })

  it('keeps failedUpdate when a manual check reports no new update', async () => {
    const controller = controllerFor('0.1.0-alpha.5')
    writeUpdateBootRecord(bootRecordPath, {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    })
    controller.evaluateBoot(false)
    expect(controller.getStatus().failedUpdate).not.toBeNull()

    await controller.check()
    const updater = (controller as unknown as { updater: FakeUpdater }).updater
    updater.emit('update-not-available', { version: '0.1.0-alpha.5' })

    const status = controller.getStatus()
    expect(status.phase).toBe('current')
    expect(status.failedUpdate).toEqual({
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4'
    })
    // The unresolved boot record stays on disk for the next boot.
    expect(readUpdateBootRecord(bootRecordPath)?.pendingVersion).toBe('0.1.0-alpha.5')
  })

  it('dismissUpdateFailure clears the record and the failure state, keeping lastGoodVersion', () => {
    const controller = controllerFor('0.1.0-alpha.5')
    writeUpdateBootRecord(bootRecordPath, {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    })
    controller.evaluateBoot(false)
    expect(controller.getStatus().failedUpdate).not.toBeNull()

    const status = controller.dismissUpdateFailure()
    expect(status.phase).toBe('current')
    expect(status.failedUpdate).toBeNull()

    const record = readUpdateBootRecord(bootRecordPath)
    expect(record?.pendingVersion).toBeNull()
    expect(record?.previousVersion).toBeNull()
    expect(record?.lastGoodVersion).toBe('0.1.0-alpha.4')
  })

  it('clears failedUpdate when a later boot accepts the update', () => {
    const controller = controllerFor('0.1.0-alpha.5')
    writeUpdateBootRecord(bootRecordPath, {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    })
    controller.evaluateBoot(false)
    expect(controller.getStatus().failedUpdate).not.toBeNull()

    controller.evaluateBoot(true)
    const status = controller.getStatus()
    expect(status.phase).toBe('current')
    expect(status.failedUpdate).toBeNull()
    expect(readUpdateBootRecord(bootRecordPath)).toMatchObject({
      pendingVersion: null,
      previousVersion: null,
      lastGoodVersion: '0.1.0-alpha.5'
    })
  })

  it('leaves status untouched when there is no boot record', () => {
    const controller = controllerFor('0.1.0-alpha.5')
    controller.evaluateBoot(false)
    expect(controller.getStatus().phase).toBe('idle')
  })
})
