import { contextBridge, ipcRenderer } from 'electron'

export type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'ready'
  | 'current'
  | 'failed'
  | 'rollback'

export interface FailedUpdateInfo {
  pendingVersion: string | null
  previousVersion: string | null
}

export interface UpdateStatus {
  phase: UpdatePhase
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
  failedUpdate: FailedUpdateInfo | null
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

const api = {
  coreState: (): Promise<{ state: string; port: number; token: string; error: string }> =>
    ipcRenderer.invoke('collie:core-state'),
  saveSecret: (provider: string, key: string): Promise<boolean> =>
    ipcRenderer.invoke('collie:save-secret', provider, key),
  stageSecretChange: (
    provider: string,
    key: string
  ): Promise<{ saved: boolean; transactionId?: string }> =>
    ipcRenderer.invoke('collie:stage-secret-change', provider, key),
  finalizeSecretChange: (transactionId: string): Promise<boolean> =>
    ipcRenderer.invoke('collie:finalize-secret-change', transactionId),
  rollbackSecretChange: (transactionId: string): Promise<boolean> =>
    ipcRenderer.invoke('collie:rollback-secret-change', transactionId),
  deleteSecret: (provider: string): Promise<boolean> =>
    ipcRenderer.invoke('collie:delete-secret', provider),
  listSecrets: (): Promise<string[]> => ipcRenderer.invoke('collie:list-secrets'),
  loadSecrets: (): Promise<Record<string, string>> =>
    ipcRenderer.invoke('collie:load-secrets'),
  pickAttachments: (): Promise<Array<{ name: string; mime: string; size: number; data_url: string }>> =>
    ipcRenderer.invoke('collie:pick-attachments'),
  pickProjectFolder: (): Promise<string | null> =>
    ipcRenderer.invoke('collie:pick-project-folder'),
  pickFileAccessFolders: (): Promise<string[]> =>
    ipcRenderer.invoke('collie:pick-file-access-folders'),
  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke('collie:open-external', url),
  showWindow: (): Promise<void> =>
    ipcRenderer.invoke('collie:show-window'),
  petCommand: (command: string): Promise<boolean> =>
    ipcRenderer.invoke('collie:pet-command', command),
  petStatus: (): Promise<{ enabled: boolean; running: boolean }> =>
    ipcRenderer.invoke('collie:pet-status'),
  setPetEnabled: (enabled: boolean): Promise<{ enabled: boolean; running: boolean }> =>
    ipcRenderer.invoke('collie:set-pet-enabled', enabled),
  updateStatus: (): Promise<UpdateStatus> =>
    ipcRenderer.invoke('collie:update-status'),
  checkForUpdate: (): Promise<UpdateStatus> =>
    ipcRenderer.invoke('collie:check-for-update'),
  downloadUpdate: (): Promise<UpdateStatus> =>
    ipcRenderer.invoke('collie:download-update'),
  restartAndInstallUpdate: (): Promise<InstallResult> =>
    ipcRenderer.invoke('collie:restart-and-install-update'),
  dismissUpdateFailure: (): Promise<UpdateStatus> =>
    ipcRenderer.invoke('collie:dismiss-update-failure'),
  updateActiveWork: (snapshot: ActiveWorkSnapshot): Promise<boolean> =>
    ipcRenderer.invoke('collie:update-active-work', snapshot),
  onUpdateStatus: (listener: (status: UpdateStatus) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: UpdateStatus): void =>
      listener(status)
    ipcRenderer.on('collie:update-status-changed', handler)
    return () => ipcRenderer.removeListener('collie:update-status-changed', handler)
  }
}

contextBridge.exposeInMainWorld('collie', api)

export type CollieBridge = typeof api
