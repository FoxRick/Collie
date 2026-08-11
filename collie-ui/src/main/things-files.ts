/**
 * Main-process file operations for the "Your things" panel.
 *
 * Trust boundary: the renderer never supplies a filesystem path. It asks for
 * a thing by (conversation_id, thing_id); the main process resolves the path
 * from the persisted ThingStore index under COLLIE_HOME — the same index the
 * core writes (`~/.collie/things/<conversation_id>.json`). A compromised
 * renderer can therefore only open / preview / copy files that Collie itself
 * registered, never arbitrary paths.
 *
 * `read` (preview) is authorized purely by the registered record, so things
 * saved from user-approved project folders preview exactly like
 * workspace-made ones. Open / Show-in-folder are user-initiated OS actions on
 * that same registered path, mirroring the existing `collie:open-external`
 * posture.
 */
import { dialog, shell } from 'electron'
import { basename, extname, isAbsolute, join } from 'path'
import { homedir } from 'os'
import { readFileSync } from 'fs'
import { copyFile, readFile, realpath, stat } from 'fs/promises'

const PREVIEW_MAX_BYTES = 8 * 1024 * 1024

// Mirrors the core's ThingStore._SAFE_CONVERSATION_ID — a conversation id can
// never smuggle path separators into the index filename.
const SAFE_CONVERSATION_ID = /^[A-Za-z0-9_-]{1,128}$/

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

/** One record of the on-disk ThingStore index (subset of the core's shape). */
interface ThingRecord {
  id?: string
  title?: string
  kind?: string
  path?: string
  size_bytes?: number
  created_at?: number | string
  status?: string
  version?: number
}

/**
 * Resolve a registered thing record from the trusted on-disk index.
 * Rejects unknown conversations/things, so renderer-supplied ids can only
 * select from records Collie actually wrote.
 */
function loadThingRecord(conversationId: string, thingId: string): ThingRecord {
  if (typeof conversationId !== 'string' || !SAFE_CONVERSATION_ID.test(conversationId)) {
    throw new Error('Not a valid conversation.')
  }
  if (typeof thingId !== 'string' || !thingId.trim()) {
    throw new Error('Not a valid thing.')
  }
  const indexPath = join(collieHomeDir(), 'things', `${conversationId}.json`)
  let payload: unknown
  try {
    payload = JSON.parse(readFileSync(indexPath, 'utf-8'))
  } catch {
    throw new Error('That thing is no longer registered.')
  }
  const records =
    payload && typeof payload === 'object' && Array.isArray((payload as { things?: unknown }).things)
      ? ((payload as { things: ThingRecord[] }).things)
      : []
  const record = records.find((entry) => entry && entry.id === thingId)
  if (!record || typeof record.path !== 'string' || !record.path.trim()) {
    throw new Error('That thing is no longer registered.')
  }
  return record
}

/** Resolve a registered thing to a real, existing file on disk. */
async function resolveRegisteredPath(
  conversationId: string,
  thingId: string
): Promise<string> {
  const record = loadThingRecord(conversationId, thingId)
  const raw = record.path as string
  if (!isAbsolute(raw)) {
    throw new Error('That thing has an invalid file path.')
  }
  const resolved = await realpath(raw)
  const stats = await stat(resolved)
  if (!stats.isFile()) {
    throw new Error('That thing is not a file anymore.')
  }
  return resolved
}

export interface ThingReadResult {
  kind: 'text' | 'image'
  text?: string
  dataUrl?: string
}

export async function thingRead(
  conversationId: string,
  thingId: string
): Promise<ThingReadResult> {
  const resolved = await resolveRegisteredPath(conversationId, thingId)
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

export async function thingOpen(
  conversationId: string,
  thingId: string
): Promise<string> {
  const resolved = await resolveRegisteredPath(conversationId, thingId)
  // Returns an error message string on failure, '' on success.
  return shell.openPath(resolved)
}

export async function thingShowInFolder(
  conversationId: string,
  thingId: string
): Promise<void> {
  const resolved = await resolveRegisteredPath(conversationId, thingId)
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
  conversationId: string,
  thingId: string
): Promise<ThingSaveCopyResult> {
  const record = loadThingRecord(conversationId, thingId)
  const resolved = await resolveRegisteredPath(conversationId, thingId)
  const options = {
    title: 'Save a copy…',
    defaultPath: join(homedir(), 'Documents', safeBaseName(resolved, record.title || ''))
  }
  const result = await dialog.showSaveDialog(options)
  if (result.canceled || !result.filePath) return { saved: false }
  await copyFile(resolved, result.filePath)
  return { saved: true, path: result.filePath }
}
