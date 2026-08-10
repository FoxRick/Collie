/**
 * Main-process file operations for the "Your things" panel.
 *
 * Thing paths travel from the core's ThingStore (validated workspace/media
 * scope at registration time) through the renderer to these handlers —
 * the panel never renders a path, it only asks for Open / Save a copy… /
 * Show in folder / in-panel preview.
 *
 * `read` (preview) is deliberately restricted to files inside the Collie
 * home tree (workspace + media + things indexes) — a compromised renderer
 * must not be able to exfiltrate arbitrary files through the preview API.
 * Open / Show-in-folder are user-initiated OS actions and accept any path
 * the core registered, exactly like the existing `collie:open-external`.
 */
import { dialog, shell } from 'electron'
import { basename, extname, isAbsolute, join, sep } from 'path'
import { homedir } from 'os'
import { copyFile, readFile, realpath, stat } from 'fs/promises'

const PREVIEW_MAX_BYTES = 8 * 1024 * 1024

const IMAGE_MIME: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.svg': 'image/svg+xml'
}

const TEXT_EXTENSIONS = new Set([
  '.md', '.markdown', '.txt', '.csv', '.json', '.html', '.htm', '.xml',
  '.yaml', '.yml', '.log', '.tsv'
])

function collieHomeDir(): string {
  return process.env.COLLIE_HOME || join(homedir(), '.collie')
}

async function resolveThingPath(raw: string): Promise<string> {
  if (typeof raw !== 'string' || !raw.trim() || !isAbsolute(raw)) {
    throw new Error('Not a valid file path.')
  }
  const resolved = await realpath(raw)
  const stats = await stat(resolved)
  if (!stats.isFile()) throw new Error('Not a file.')
  return resolved
}

function insideCollieHome(resolved: string): boolean {
  const home = join(collieHomeDir()) + sep
  return resolved.startsWith(home)
}

export interface ThingReadResult {
  kind: 'text' | 'image'
  text?: string
  dataUrl?: string
}

export async function thingRead(rawPath: string): Promise<ThingReadResult> {
  const resolved = await resolveThingPath(rawPath)
  if (!insideCollieHome(resolved)) {
    throw new Error('Preview is only available for files Collie made in its workspace.')
  }
  const stats = await stat(resolved)
  if (stats.size > PREVIEW_MAX_BYTES) {
    throw new Error('That file is too large to preview here — use Open instead.')
  }
  const ext = extname(resolved).toLowerCase()
  const mime = IMAGE_MIME[ext]
  if (mime) {
    const data = await readFile(resolved)
    return { kind: 'image', dataUrl: `data:${mime};base64,${data.toString('base64')}` }
  }
  if (TEXT_EXTENSIONS.has(ext)) {
    const text = await readFile(resolved, 'utf-8')
    return { kind: 'text', text: text.slice(0, 200_000) }
  }
  throw new Error('No in-panel preview for this file type — use Open instead.')
}

export async function thingOpen(rawPath: string): Promise<string> {
  const resolved = await resolveThingPath(rawPath)
  // Returns an error message string on failure, '' on success.
  return shell.openPath(resolved)
}

export async function thingShowInFolder(rawPath: string): Promise<void> {
  const resolved = await resolveThingPath(rawPath)
  shell.showItemInFolder(resolved)
}

function safeBaseName(rawPath: string, title: string): string {
  const ext = extname(rawPath)
  const cleaned = (title || basename(rawPath, ext) || 'thing')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return cleaned ? `${cleaned}${ext}` : basename(rawPath)
}

export interface ThingSaveCopyResult {
  saved: boolean
  path?: string
}

export async function thingSaveCopy(
  rawPath: string,
  title: string
): Promise<ThingSaveCopyResult> {
  const resolved = await resolveThingPath(rawPath)
  const options = {
    title: 'Save a copy…',
    defaultPath: join(homedir(), 'Documents', safeBaseName(resolved, title))
  }
  const result = await dialog.showSaveDialog(options)
  if (result.canceled || !result.filePath) return { saved: false }
  await copyFile(resolved, result.filePath)
  return { saved: true, path: result.filePath }
}
