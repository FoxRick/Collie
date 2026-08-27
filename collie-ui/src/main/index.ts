import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, shell, nativeImage, session } from 'electron'
import { basename, extname, isAbsolute, join } from 'path'
import { homedir } from 'os'
import { pathToFileURL } from 'url'
import { appendFileSync, existsSync, mkdirSync, writeFileSync } from 'fs'
import { readFile as readFileAsync, realpath as realpathAsync, stat as statAsync } from 'fs/promises'
import { assertLocalWindowsFileAccessFolder } from './local-file-access'
import { spawnCore, stopCore, coreState, onCoreReady } from './python'
import { petEnabled, petRunning, setPetEnabled, spawnPet, stopPet } from './pet'
import { isAllowedPetCommand } from './petCommands'
import {
  thingOpen,
  thingRead,
  thingSaveCopy,
  thingShowInFolder
} from './things-files'
import {
  deleteSecret,
  finalizeSecretChange,
  listSecretProviders,
  rollbackSecretChange,
  saveSecret,
  secureStorageAvailable,
  stageSecretChange
} from './secrets'
import {
  coreSend,
  onCoreEvent,
  pushStoredSecretsToCore,
  stopCoreBroker,
  type CoreCommandFrame
} from './core-client'
import { startKeychainServer, stopKeychainServer } from './keychain-server'
import { getAccountState, signOut, startAccountSignIn } from './account-auth'
import {
 enableSync,
 getSyncStatus,
 listSnapshots,
 restoreFromDevice,
 uploadSnapshot
} from './cloud-sync'
import { autoUpdater } from 'electron-updater'
import {
  ActiveWorkTracker,
  UpdateController,
  type ActiveWorkSnapshot
} from './updater-controller'
import {
  guardIpcHandler,
  isSafeExternalUrl,
  isTrustedIpcSender,
  isTrustedRendererUrl,
  shouldAllowAudioPermission
} from './renderer-security'
import { RendererRecoverySupervisor } from './renderer-recovery'
import {
  createCoreSettleMachine,
  sampleCoreSettle,
  DEFAULT_CORE_SETTLE_CONFIG,
  type CoreSettleState
} from './update-boot-settle'
import { readUpdateBootRecord } from './update-boot-record'

// A detached dev terminal can close stdout while Electron is still running.
// Treat that transport failure as harmless instead of surfacing an app error dialog.
function ignoreBrokenPipe(stream: NodeJS.WriteStream): void {
  stream.on('error', (error: NodeJS.ErrnoException) => {
    if (error.code === 'EPIPE') return
  })
}

ignoreBrokenPipe(process.stdout)
ignoreBrokenPipe(process.stderr)

/**
 * Crash breadcrumbs: when the main process dies hard (uncaught exception,
 * unhandled rejection) there is usually no Crashpad dump to diagnose from —
 * the window is simply gone and the Python core is orphaned on its port.
 * Record the failure to userData/crash-breadcrumbs.log before exiting so the
 * next incident has evidence, and quit cleanly (fires will-quit → stopCore)
 * instead of dying mid-state.
 */
function writeCrashBreadcrumb(kind: string, detail: string): void {
  try {
    const dir = app.getPath('userData')
    mkdirSync(dir, { recursive: true })
    appendFileSync(
      join(dir, 'crash-breadcrumbs.log'),
      `${new Date().toISOString()} ${kind}: ${detail}\n`
    )
  } catch {
    // Logging must never be the thing that crashes.
  }
}

process.on('uncaughtException', (error) => {
  writeCrashBreadcrumb('uncaughtException', error?.stack || String(error))
  // Let the app exit on its own terms so will-quit stops the core cleanly.
  const forceExit = setTimeout(() => process.exit(1), 3000)
  forceExit.unref()
  app.quit()
})

process.on('unhandledRejection', (reason) => {
  writeCrashBreadcrumb(
    'unhandledRejection',
    reason instanceof Error ? reason.stack || reason.message : String(reason)
  )
})

const isDev = !app.isPackaged
const devRendererUrl = process.env.ELECTRON_RENDERER_URL?.trim()
const packagedRendererPath = join(__dirname, '../renderer/index.html')
const trustedRendererUrl = isDev && devRendererUrl
  ? new URL(devRendererUrl).href
  : pathToFileURL(packagedRendererPath).href
const isolatedUserData = process.env.COLLIE_ELECTRON_USER_DATA?.trim()
if (isolatedUserData) app.setPath('userData', isolatedUserData)

// Durable record of "is an update pending / did the last one boot healthy".
const bootRecordPath = join(app.getPath('userData'), 'update-boot-record.json')

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false
const rendererRecoverySupervisor = new RendererRecoverySupervisor({
  reloadWindow: () => {
    // Re-check app state before acting: the timer may have outlived a quit
    // or the window it was scheduled for may have been replaced.
    if (quitting) return
    if (!mainWindow || mainWindow.isDestroyed()) {
      // A hard renderer/GPU crash can take the whole window frame down. The
      // app is still alive in the tray — recreate the window instead of
      // leaving an invisible app with a live core behind it.
      createWindow()
      return
    }
    mainWindow.webContents.reload()
  },
  scheduleReload: (fn, delayMs) => setTimeout(fn, delayMs),
  cancelReload: (handle) => {
    if (handle) clearTimeout(handle as NodeJS.Timeout)
  },
  showDialog: () => {
    dialog.showErrorBox(
      'Collie needs a restart',
      'The window crashed several times in a row. Please quit and reopen Collie — your chats and work are safe.'
    )
  },
  now: Date.now
})
let updateCheckTimer: NodeJS.Timeout | null = null
const activeWork = new ActiveWorkTracker()
const updates = new UpdateController(
  autoUpdater,
  app.getVersion(),
  app.isPackaged,
  activeWork,
  () => {
    quitting = true
  },
  bootRecordPath
)

function sendPetCommand(command: string): boolean {
  try {
    const dir = process.env.COLLIE_HOME?.trim() || join(homedir(), '.collie')
    mkdirSync(dir, { recursive: true })
    writeFileSync(
      join(dir, 'pet_command.json'),
      JSON.stringify({ command, ts: Date.now() / 1000 }),
      'utf8'
    )
    return true
  } catch {
    return false
  }
}

const ATTACHMENT_MIME: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.pdf': 'application/pdf',
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.csv': 'text/csv',
  '.json': 'application/json',
  '.html': 'text/html',
  '.xml': 'application/xml',
  '.yaml': 'application/yaml',
  '.yml': 'application/yaml',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
}

interface SelectedAttachment {
  name: string
  mime: string
  size: number
  data_url: string
}

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.show()
      mainWindow.focus()
    }
  })
}

function appIconPath(): string {
  return isDev
    ? join(app.getAppPath(), 'build', 'icon.png')
    : join(process.resourcesPath, 'icon.png')
}

function createWindow(): void {
  // A pending reload belongs to the previous window (if any); a replacement
  // window must never be reloaded by a stale timer from its predecessor.
  rendererRecoverySupervisor.cancelPendingReload()
  const iconPath = appIconPath()
  mainWindow = new BrowserWindow({
    width: 1120,
    height: 780,
    minWidth: 760,
    minHeight: 520,
    show: true,
    center: true,
    title: 'Collie',
    icon: existsSync(iconPath) ? iconPath : undefined,
    backgroundColor: '#FAFAFA',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // The preload only uses contextBridge + ipcRenderer, so the renderer
      // can run sandboxed (Electron's secure default).
      sandbox: true
    }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isTrustedRendererUrl(url, trustedRendererUrl)) {
      event.preventDefault()
    }
  })

  // Mic access (audio dictation) for the app's own window; deny everything
  // else (notifications are handled natively and do not need this).
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    callback(
      shouldAllowAudioPermission(
        webContents,
        mainWindow?.webContents ?? null,
        permission,
        details,
        trustedRendererUrl
      )
    )
  })

  mainWindow.on('ready-to-show', () => { console.log('[window] ready-to-show'); mainWindow?.show() })
  mainWindow.on('show', () => console.log('[window] show event'))
  mainWindow.on('hide', () => console.log('[window] hide event'))
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error('[window] render-process-gone reason:', details.reason, 'exitCode:', details.exitCode)
    if (quitting) return
    rendererRecoverySupervisor.renderProcessGone(details.reason)
  })
  mainWindow.on('focus', () => {
    // Returning to the app is an acknowledgement that the user reviewed
    // the latest completion announcement.
    sendPetCommand('status:dismiss')
  })

  mainWindow.on('close', (e) => {
    // Minimize to tray instead of quitting (F009)
    if (!quitting && process.platform !== 'darwin') {
      e.preventDefault()
      mainWindow?.hide()
    }
    // A hidden or closing window must not be reloaded by a pending timer.
    rendererRecoverySupervisor.cancelPendingReload()
  })

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    // Aligned with `collie:open-external`: https only, no credentials.
    if (isSafeExternalUrl(url)) {
      shell.openExternal(url)
    }
    return { action: 'deny' }
  })

  if (isDev && devRendererUrl) {
    mainWindow.loadURL(trustedRendererUrl)
  } else {
    mainWindow.loadFile(packagedRendererPath)
  }
}

function createTray(): void {
  const path = appIconPath()
  const icon = existsSync(path) ? nativeImage.createFromPath(path).resize({ width: 24, height: 24 }) : nativeImage.createEmpty()
  try {
    tray = new Tray(icon)
  } catch {
    return // tray is best-effort; some environments have none
  }
  tray.setToolTip('Collie — your personal AI, with a dog')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Open Collie',
        click: () => {
          mainWindow?.show()
          mainWindow?.focus()
        }
      },
      { type: 'separator' },
      {
        label: 'Quit Collie',
        click: () => {
          quitting = true
          app.quit()
        }
      }
    ])
  )
  tray.on('click', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })
}

function registerIpc(): void {
  // Only the app's own top-level frame at the exact renderer URL may call IPC.
  const validSender = (event: Electron.IpcMainInvokeEvent): boolean => {
    return isTrustedIpcSender(
      event.senderFrame,
      mainWindow?.webContents.mainFrame ?? null,
      trustedRendererUrl
    )
  }
  const handle = <T extends unknown[]>(
    channel: string,
    handler: (...args: T) => unknown | Promise<unknown>
  ): void => {
    ipcMain.handle(channel, guardIpcHandler(validSender, handler))
  }

  handle('collie:core-state', () => coreState())
  // #122: the renderer no longer holds the per-boot token or opens its own
  // socket to the core. Every command is relayed here, over main's single
  // authenticated connection (core-client.ts CoreBroker), guarded by the
  // exact renderer-URL sender check.
  handle('collie:core-send', (frame: CoreCommandFrame): Promise<unknown> => coreSend(frame))
  handle('collie:secure-storage-status', () => ({
    available: secureStorageAvailable(),
    platform: process.platform
  }))
  handle('collie:save-secret', (provider: string, key: string) =>
    saveSecret(provider, key)
  )
  handle('collie:stage-secret-change', (provider: string, key: string) =>
    stageSecretChange(provider, key)
  )
  handle('collie:finalize-secret-change', (transactionId: string) =>
    finalizeSecretChange(transactionId)
  )
  handle('collie:rollback-secret-change', (transactionId: string) =>
    rollbackSecretChange(transactionId)
  )
  handle('collie:delete-secret', (provider: string) => deleteSecret(provider))
  handle('collie:list-secrets', () => listSecretProviders())
  // Count only — the values themselves are pushed to the core by the main
  // process (core-client.ts); decrypted secrets never cross into the renderer.
  handle('collie:stored-secret-count', () => listSecretProviders().length)
  handle('account:start-sign-in', () => startAccountSignIn())
  handle('account:get-state', () => getAccountState())
  handle('account:sign-out', () => signOut())
  // Account cloud sync (account-cloud-sync.md): per-device snapshots,
  // opt-in. Payloads never include secrets; RLS scopes every REST call.
  handle('account:sync-status', () => getSyncStatus())
  handle('account:sync-enable', (enabled: boolean) => enableSync(Boolean(enabled)))
  handle('account:sync-upload', () => uploadSnapshot())
  handle('account:sync-list', () => listSnapshots())
  handle('account:sync-restore', (deviceId: string) => restoreFromDevice(String(deviceId)))
  handle('collie:pick-attachments', async (): Promise<SelectedAttachment[]> => {
    const options = {
      title: 'Attach files to your message',
      properties: ['openFile', 'multiSelections'] as Array<'openFile' | 'multiSelections'>,
      filters: [
        {
          name: 'Images and documents',
          extensions: [
            'png', 'jpg', 'jpeg', 'webp', 'gif', 'pdf', 'txt', 'md', 'csv', 'json',
            'html', 'xml', 'yaml', 'yml', 'docx', 'xlsx', 'pptx'
          ]
        }
      ]
    }
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options)
    if (result.canceled) return []
    if (result.filePaths.length > 4) {
      throw new Error('Choose up to four files at a time.')
    }
    let total = 0
    const attachments: SelectedAttachment[] = []
    for (const path of result.filePaths) {
      const stats = await statAsync(path)
      if (stats.size > 6 * 1024 * 1024) throw new Error(`${basename(path)} is larger than 6 MB.`)
      total += stats.size
      if (total > 24 * 1024 * 1024) throw new Error('Keep the total attachment size under 24 MB.')
      const mime = ATTACHMENT_MIME[extname(path).toLowerCase()]
      if (!mime) throw new Error(`${basename(path)} is not a supported file type.`)
      const data = await readFileAsync(path)
      attachments.push({
        name: basename(path),
        mime,
        size: stats.size,
        data_url: `data:${mime};base64,${data.toString('base64')}`
      })
    }
    return attachments
  })
  handle('collie:pick-project-folder', async (): Promise<string | null> => {
    const options = {
      title: 'Choose a project folder',
      buttonLabel: 'Use this folder',
      properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'>
    }
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options)
    return result.canceled ? null : result.filePaths[0] || null
  })
  handle('collie:pick-file-access-folders', async (): Promise<string[]> => {
    const options = {
      title: 'Choose folders Collie can use',
      buttonLabel: 'Allow these folders',
      properties: ['openDirectory', 'multiSelections', 'createDirectory'] as Array<
        'openDirectory' | 'multiSelections' | 'createDirectory'
      >
    }
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options)
    if (result.canceled) return []
    if (result.filePaths.length > 16) throw new Error('Choose up to 16 folders at a time.')

    const folders: string[] = []
    const seen = new Set<string>()
    for (const selectedPath of result.filePaths) {
      if (!isAbsolute(selectedPath) || selectedPath.startsWith('\\\\')) {
        throw new Error('Choose a folder stored on this computer.')
      }
      const canonicalPath = await realpathAsync(selectedPath)
      if (canonicalPath.startsWith('\\\\')) {
        throw new Error('Choose a folder stored on this computer.')
      }
      await assertLocalWindowsFileAccessFolder(canonicalPath)
      const stats = await statAsync(canonicalPath)
      if (!stats.isDirectory()) throw new Error(`${basename(selectedPath)} is not a folder.`)
      const key = canonicalPath.toLowerCase()
      if (!seen.has(key)) {
        seen.add(key)
        folders.push(canonicalPath)
      }
    }
    return folders
  })
  handle('collie:open-external', (url: string) => {
    if (isSafeExternalUrl(url)) shell.openExternal(url)
  })
  handle('collie:show-window', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
  handle('collie:pet-command', (command: string): boolean => {
    // The pet process polls ~/.collie/pet_command.json (F078)
    if (!isAllowedPetCommand(command)) return false
    // "show" also revives the pet if its process isn't running
    if (command === 'show' && !petRunning()) {
      spawnPet(isDev)
    }
    return sendPetCommand(command)
  })
  handle('collie:pet-status', () => ({
    enabled: petEnabled(),
    running: petRunning()
  }))
  handle('collie:set-pet-enabled', (enabled: boolean) => {
    if (typeof enabled !== 'boolean') return { enabled: petEnabled(), running: petRunning() }
    setPetEnabled(enabled)
    if (enabled) spawnPet(isDev)
    else stopPet()
    return { enabled: petEnabled(), running: petRunning() }
  })
  handle('collie:update-status', () => updates.getStatus())
  handle('collie:check-for-update', () => updates.check())
  handle('collie:download-update', () => updates.download())
  handle('collie:restart-and-install-update', () => updates.restartAndInstall())
  handle('collie:dismiss-update-failure', () => updates.dismissUpdateFailure())
  handle('collie:update-active-work', (snapshot: ActiveWorkSnapshot) => {
    if (!isActiveWorkSnapshot(snapshot)) return false
    activeWork.update(clampActiveWork(snapshot))
    return true
  })
  handle('collie:thing-read', (conversationId: string, thingId: string) =>
    thingRead(conversationId, thingId))
  handle('collie:thing-open', (conversationId: string, thingId: string) =>
    thingOpen(conversationId, thingId))
  handle('collie:thing-show-in-folder', (conversationId: string, thingId: string) =>
    thingShowInFolder(conversationId, thingId))
  handle('collie:thing-save-copy', (conversationId: string, thingId: string) =>
    thingSaveCopy(conversationId, thingId))
}

function clampActiveWork(snapshot: ActiveWorkSnapshot): ActiveWorkSnapshot {
  // Bounded counters: absurd values (e.g. after a renderer desync) must not
  // push the update-blocker into a lie about active work.
  const cap = (n: number): number => (Number.isFinite(n) ? Math.min(1000, Math.max(0, n)) : 0)
  return {
    chats: cap(snapshot.chats),
    approvals: cap(snapshot.approvals),
    routines: cap(snapshot.routines),
    externalActions: cap(snapshot.externalActions)
  }
}

/**
 * Wait for the Python core to reach a settled state after spawnCore, so the
 * post-update boot verification can judge the new version honestly.
 *
 * The policy lives in update-boot-settle.ts (pure, unit-tested). When a
 * pending update record matches the running version we require the core to
 * stay `running` continuously for the probation window — a single ready
 * message is NOT sustained health, and accepting a core that crashes seconds
 * later would permanently clear the boot record and hide the notice. On a
 * normal first boot there is no probation and the first `running` sample
 * resolves immediately so startup is not delayed.
 */
async function waitForCoreSettle(requireProbation: boolean): Promise<'running' | 'failed'> {
  const config = DEFAULT_CORE_SETTLE_CONFIG
  return new Promise((resolve) => {
    let machine = createCoreSettleMachine(Date.now(), requireProbation)
    const poll = (): void => {
      machine = sampleCoreSettle(
        machine,
        coreState().state as CoreSettleState,
        Date.now(),
        config
      )
      if (machine.verdict !== null) {
        resolve(machine.verdict)
        return
      }
      setTimeout(poll, config.pollIntervalMs)
    }
    poll()
  })
}

function isActiveWorkSnapshot(value: unknown): value is ActiveWorkSnapshot {
  if (!value || typeof value !== 'object') return false
  const snapshot = value as Record<string, unknown>
  return ['chats', 'approvals', 'routines', 'externalActions'].every(
    (key) =>
      typeof snapshot[key] === 'number' &&
      Number.isFinite(snapshot[key]) &&
      (snapshot[key] as number) >= 0
  )
}

app.whenReady().then(async () => {
  // Windows toasts (OS notifications) require an App User Model ID before
  // any notification is created, or they are silently dropped.
  if (process.platform === 'win32') {
    app.setAppUserModelId('com.collie.desktop')
  }
  registerIpc()
  createWindow()
  // #122: forward every core-pushed event (ready/delta/message/card/
  // approval_*, connection_opened, ...) to the app's own renderer. The
  // renderer no longer opens a socket to the core, so this is the only
  // path by which it learns about core activity.
  onCoreEvent((event) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('collie:core-event', event)
    }
  })
  createTray()
  updates.onStatus((status) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('collie:update-status-changed', status)
    }
  })
  // Stand up the OS keychain bridge BEFORE spawning the core so python.ts can
  // hand the bridge address to the core via env (see spawnCore). When no real
  // keyring backend is available the server does not start and the connector
  // catalog honestly gates its routes to coming-soon.
  await startKeychainServer()
  await spawnCore(isDev)
  // Push stored secrets to the core over the main process's own connection
  // (the renderer never sees decrypted values).
  onCoreReady(() => {
    void pushStoredSecretsToCore()
  })
  // If this boot was an update, verify the new version came up healthy
  // before letting the update ledger treat it as last-known-good. Probation
  // (sustained running, not just a ready message) applies only when a
  // pending update record matches the version that just started.
  const bootRecord = readUpdateBootRecord(bootRecordPath)
  const requireProbation = bootRecord?.pendingVersion === app.getVersion()
  const coreHealthy = (await waitForCoreSettle(requireProbation)) === 'running'
  updates.evaluateBoot(coreHealthy)
  if (petEnabled()) spawnPet(isDev)
  // Let the window and local core settle before contacting the release channel.
  // A pending failed-update notice keeps the stage: the user drives the next check.
  updateCheckTimer = setTimeout(() => {
    updateCheckTimer = null
    if (!updates.getStatus().failedUpdate) {
      void updates.check()
    }
  }, 15_000)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
    else mainWindow?.show()
  })
})

app.on('before-quit', () => {
  quitting = true
  // A pending reload must not fire after the app has started quitting.
  rendererRecoverySupervisor.cancelPendingReload()
  if (updateCheckTimer) {
    clearTimeout(updateCheckTimer)
    updateCheckTimer = null
  }
})

app.on('will-quit', () => {
  stopCore()
  stopCoreBroker()
  stopPet()
  stopKeychainServer()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // stay in tray
  }
})
