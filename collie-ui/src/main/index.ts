import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, shell, nativeImage, session } from 'electron'
import { basename, extname, isAbsolute, join } from 'path'
import { homedir } from 'os'
import { pathToFileURL } from 'url'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { readFile as readFileAsync, realpath as realpathAsync, stat as statAsync } from 'fs/promises'
import { assertLocalWindowsFileAccessFolder } from './local-file-access'
import { spawnCore, stopCore, coreState } from './python'
import { petEnabled, petRunning, setPetEnabled, spawnPet, stopPet } from './pet'
import { isAllowedPetCommand } from './petCommands'
import {
  deleteSecret,
  finalizeSecretChange,
  listSecretProviders,
  loadSecrets,
  rollbackSecretChange,
  saveSecret,
  stageSecretChange
} from './secrets'
import { autoUpdater } from 'electron-updater'
import {
  ActiveWorkTracker,
  UpdateController,
  type ActiveWorkSnapshot
} from './updater-controller'
import {
  guardIpcHandler,
  isTrustedIpcSender,
  isTrustedRendererUrl,
  shouldAllowAudioPermission
} from './renderer-security'
import {
  INITIAL_RENDERER_RECOVERY_STATE,
  isRecoverableRendererReason,
  planRendererRecovery,
  type RendererRecoveryState
} from './renderer-recovery'

// A detached dev terminal can close stdout while Electron is still running.
// Treat that transport failure as harmless instead of surfacing an app error dialog.
function ignoreBrokenPipe(stream: NodeJS.WriteStream): void {
  stream.on('error', (error: NodeJS.ErrnoException) => {
    if (error.code === 'EPIPE') return
  })
}

ignoreBrokenPipe(process.stdout)
ignoreBrokenPipe(process.stderr)

const isDev = !app.isPackaged
const devRendererUrl = process.env.ELECTRON_RENDERER_URL?.trim()
const packagedRendererPath = join(__dirname, '../renderer/index.html')
const trustedRendererUrl = isDev && devRendererUrl
  ? new URL(devRendererUrl).href
  : pathToFileURL(packagedRendererPath).href
const isolatedUserData = process.env.COLLIE_ELECTRON_USER_DATA?.trim()
if (isolatedUserData) app.setPath('userData', isolatedUserData)

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false
let rendererRecovery: RendererRecoveryState = INITIAL_RENDERER_RECOVERY_STATE
let updateCheckTimer: NodeJS.Timeout | null = null
const activeWork = new ActiveWorkTracker()
const updates = new UpdateController(
  autoUpdater,
  app.getVersion(),
  app.isPackaged,
  activeWork,
  () => {
    quitting = true
  }
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
    const plan = planRendererRecovery(rendererRecovery, Date.now(), details.reason)
    if (!plan) {
      // A genuine crash with the reload budget exhausted: a reload would just
      // loop. The Python core is untouched, so a full restart loses nothing.
      if (isRecoverableRendererReason(details.reason)) {
        dialog.showErrorBox(
          'Collie needs a restart',
          'The window crashed several times in a row. Please quit and reopen Collie — your chats and work are safe.'
        )
      }
      return
    }
    rendererRecovery = plan.next
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.reload()
      }
    }, plan.delayMs)
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
  })

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) {
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
  handle('collie:load-secrets', () => loadSecrets())
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
    if (url.startsWith('https://')) shell.openExternal(url)
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
  handle('collie:update-active-work', (snapshot: ActiveWorkSnapshot) => {
    if (!isActiveWorkSnapshot(snapshot)) return false
    activeWork.update(clampActiveWork(snapshot))
    return true
  })
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
  createTray()
  updates.onStatus((status) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('collie:update-status-changed', status)
    }
  })
  await spawnCore(isDev)
  if (petEnabled()) spawnPet(isDev)
  // Let the window and local core settle before contacting the release channel.
  updateCheckTimer = setTimeout(() => {
    updateCheckTimer = null
    void updates.check()
  }, 15_000)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
    else mainWindow?.show()
  })
})

app.on('before-quit', () => {
  quitting = true
  if (updateCheckTimer) {
    clearTimeout(updateCheckTimer)
    updateCheckTimer = null
  }
})

app.on('will-quit', () => {
  stopCore()
  stopPet()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // stay in tray
  }
})
