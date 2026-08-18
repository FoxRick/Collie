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

export interface AccountState {
  signedIn: boolean
  email: string | null
  /** Epoch milliseconds, or null when unknown. */
  expiresAt: number | null
}

export interface InstallResult {
  installed: boolean
  blockedBy: string[]
}

const api = {
  coreState: (): Promise<{ state: string; port: number; token: string; error: string }> =>
    ipcRenderer.invoke('collie:core-state'),
  secureStorageStatus: (): Promise<{ available: boolean; platform: string }> =>
    ipcRenderer.invoke('collie:secure-storage-status'),
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
  storedSecretCount: (): Promise<number> =>
    ipcRenderer.invoke('collie:stored-secret-count'),
  pickAttachments: (): Promise<Array<{ name: string; mime: string; size: number; data_url: string }>> =>
    ipcRenderer.invoke('collie:pick-attachments'),
  pickProjectFolder: (): Promise<string | null> =>
    ipcRenderer.invoke('collie:pick-project-folder'),
  pickFileAccessFolders: (): Promise<string[]> =>
    ipcRenderer.invoke('collie:pick-file-access-folders'),
  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke('collie:open-external', url),
  thingRead: (conversationId: string, thingId: string): Promise<{ kind: 'text' | 'image'; text?: string; dataUrl?: string }> =>
    ipcRenderer.invoke('collie:thing-read', conversationId, thingId),
  thingOpen: (conversationId: string, thingId: string): Promise<string> =>
    ipcRenderer.invoke('collie:thing-open', conversationId, thingId),
  thingShowInFolder: (conversationId: string, thingId: string): Promise<void> =>
    ipcRenderer.invoke('collie:thing-show-in-folder', conversationId, thingId),
  thingSaveCopy: (
    conversationId: string,
    thingId: string
  ): Promise<{ saved: boolean; path?: string }> =>
    ipcRenderer.invoke('collie:thing-save-copy', conversationId, thingId),
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

const accountApi = {
  startSignIn: (): Promise<AccountState> => ipcRenderer.invoke('account:start-sign-in'),
  getState: (): Promise<AccountState> => ipcRenderer.invoke('account:get-state'),
  signOut: (): Promise<AccountState> => ipcRenderer.invoke('account:sign-out')
}

contextBridge.exposeInMainWorld('collie', api)
contextBridge.exposeInMainWorld('account', accountApi)

export type CollieBridge = typeof api
export type AccountBridge = typeof accountApi
