import type { EventEmitter } from 'events'

export type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'ready'
  | 'current'
  | 'failed'

export interface UpdateStatus {
  phase: UpdatePhase
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
}

export interface ActiveWorkSnapshot {
  chats: number
  approvals: number
  routines: number
  externalActions: number
}

export interface InstallResult {
  installed: boolean
  blockedBy: string[]
}

interface UpdateInfo {
  version: string
}

interface ProgressInfo {
  percent: number
}

export interface AutoUpdaterLike extends EventEmitter {
  autoDownload: boolean
  autoInstallOnAppQuit: boolean
  allowPrerelease: boolean
  channel: string | null
  checkForUpdates(): Promise<unknown>
  downloadUpdate(): Promise<unknown>
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void
}

export class ActiveWorkTracker {
  private snapshot: ActiveWorkSnapshot | null = null

  update(snapshot: ActiveWorkSnapshot): void {
    this.snapshot = {
      chats: Math.max(0, Math.floor(snapshot.chats)),
      approvals: Math.max(0, Math.floor(snapshot.approvals)),
      routines: Math.max(0, Math.floor(snapshot.routines)),
      externalActions: Math.max(0, Math.floor(snapshot.externalActions))
    }
  }

  blockers(): string[] {
    if (!this.snapshot) return ['activity status unavailable']
    const blockers: string[] = []
    if (this.snapshot.chats) blockers.push('active chat')
    if (this.snapshot.approvals) blockers.push('pending approval')
    if (this.snapshot.routines) blockers.push('running routine')
    if (this.snapshot.externalActions) blockers.push('active external action')
    return blockers
  }
}

export class UpdateController {
  private status: UpdateStatus
  private readonly listeners = new Set<(status: UpdateStatus) => void>()

  constructor(
    private readonly updater: AutoUpdaterLike,
    currentVersion: string,
    private readonly enabled: boolean,
    private readonly activeWork: ActiveWorkTracker,
    private readonly beforeInstall: () => void = () => undefined
  ) {
    this.status = { phase: 'idle', currentVersion }
    updater.autoDownload = false
    updater.autoInstallOnAppQuit = false
    updater.allowPrerelease = true
    updater.channel = 'alpha'

    updater.on('checking-for-update', () => this.setStatus({ phase: 'checking' }))
    updater.on('update-available', (info: UpdateInfo) =>
      this.setStatus({ phase: 'available', availableVersion: info.version })
    )
    updater.on('update-not-available', (info: UpdateInfo) =>
      this.setStatus({
        phase: 'current',
        availableVersion: info.version,
        message: 'Collie is up to date.'
      })
    )
    updater.on('download-progress', (progress: ProgressInfo) =>
      this.setStatus({ phase: 'downloading', percent: Math.max(0, Math.min(100, progress.percent)) })
    )
    updater.on('update-downloaded', (info: UpdateInfo) =>
      this.setStatus({ phase: 'ready', availableVersion: info.version, percent: 100 })
    )
    updater.on('error', (error: Error) =>
      this.setStatus({ phase: 'failed', message: safeError(error) })
    )
  }

  getStatus(): UpdateStatus {
    return { ...this.status }
  }

  onStatus(listener: (status: UpdateStatus) => void): () => void {
    this.listeners.add(listener)
    listener(this.getStatus())
    return () => this.listeners.delete(listener)
  }

  async check(): Promise<UpdateStatus> {
    if (!this.enabled) {
      this.setStatus({
        phase: 'current',
        message: 'Update checks are available in the installed app.'
      })
      return this.getStatus()
    }
    this.setStatus({ phase: 'checking' })
    try {
      await this.updater.checkForUpdates()
    } catch (error) {
      this.setStatus({ phase: 'failed', message: safeError(error) })
    }
    return this.getStatus()
  }

  async download(): Promise<UpdateStatus> {
    if (this.status.phase !== 'available') return this.getStatus()
    this.setStatus({ phase: 'downloading', percent: 0 })
    try {
      await this.updater.downloadUpdate()
    } catch (error) {
      this.setStatus({ phase: 'failed', message: safeError(error) })
    }
    return this.getStatus()
  }

  restartAndInstall(): InstallResult {
    if (this.status.phase !== 'ready') return { installed: false, blockedBy: ['update not ready'] }
    const blockedBy = this.activeWork.blockers()
    if (blockedBy.length) return { installed: false, blockedBy }
    this.beforeInstall()
    this.updater.quitAndInstall(false, true)
    return { installed: true, blockedBy: [] }
  }

  private setStatus(next: Omit<UpdateStatus, 'currentVersion'>): void {
    this.status = {
      currentVersion: this.status.currentVersion,
      availableVersion: next.availableVersion ?? this.status.availableVersion,
      ...next
    }
    const status = this.getStatus()
    for (const listener of this.listeners) listener(status)
  }
}

function safeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return (
    message
      .replace(/https?:\/\/\S+/gi, '[update server]')
      .replace(/[A-Za-z]:\\[^\s]+/g, '[local path]')
      .replace(/\b(token|key|secret|password)=\S+/gi, '$1=[redacted]')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 240) || 'Update failed.'
  )
}
