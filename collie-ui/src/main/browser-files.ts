/** Main-process picker: the renderer/model never supplies a filesystem path. */
import { dialog, type BrowserWindow } from 'electron'
import { commandWithCore } from './core-client'
import type { BrowserCommand, BrowserResult } from '../shared/browser'

export async function browserFileAction(window: BrowserWindow | null, conversationId: string,
  command: BrowserCommand, direction: 'upload' | 'download'): Promise<BrowserResult | null> {
  if (!conversationId || conversationId.length > 200 || !command.session_id || !command.ref || !command.observation_id) {
    throw new Error('Read the browser page and choose a file control first.')
  }
  const filters = [{ name: 'Documents and images', extensions: ['txt', 'csv', 'pdf', 'png', 'jpg', 'jpeg', 'webp', 'docx', 'xlsx', 'pptx'] }]
  let path: string | undefined
  if (direction === 'upload') {
    const options = { title: 'Choose a file to upload to this browser', properties: ['openFile'] as ['openFile'], filters }
    const picked = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options)
    if (picked.canceled) return null
    path = picked.filePaths[0]
  } else {
    const options = { title: 'Save public browser download (up to 6 MB)', defaultPath: 'download.pdf', filters }
    const picked = window ? await dialog.showSaveDialog(window, options) : await dialog.showSaveDialog(options)
    if (picked.canceled) return null
    path = picked.filePath
  }
  if (!path) return null
  return await commandWithCore('browser_action', { conversation_id: conversationId,
    command: { operation: direction, session_id: command.session_id, ref: command.ref,
      observation_id: command.observation_id, file_path: path } }) as BrowserResult
}
