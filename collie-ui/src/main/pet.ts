/**
 * Desktop pet lifecycle (F070, F078).
 *
 * The Border Collie is a separate Python/tkinter process
 * (`python -m collie_core.pet`). The Electron main process owns it:
 * spawned at app start (unless disabled in pet_settings.json), revived by
 * "show", respawned after crashes, and put to bed when the app quits.
 *
 * Release gate: the desktop pet is NOT shipping yet (F070/F078 are built and
 * tested, but not needed in the alpha release). All pet code and assets stay
 * in the repo — flip PET_AVAILABLE to true to re-enable the whole feature
 * (spawn, IPC, and the Settings tab render it again). pet_settings.json
 * overrides still apply once the gate is open.
 */

const PET_AVAILABLE = false

import { ChildProcess, spawn } from 'child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { coreRoot, findPython } from './python'

let child: ChildProcess | null = null
let stopping = false
let crashRestarts = 0
let spawnedAt = 0
let respawnTimer: NodeJS.Timeout | null = null

export function petRunning(): boolean {
  return child !== null
}

export function petEnabled(): boolean {
  if (!PET_AVAILABLE) return false
  // Respect an "enabled": false flag in the Collie home dir
  // (COLLIE_HOME-consistent; matches index.ts sendPetCommand).
  try {
    const settingsPath = join(petSettingsDir(), 'pet_settings.json')
    if (existsSync(settingsPath)) {
      const data = JSON.parse(readFileSync(settingsPath, 'utf-8'))
      if (data && data.enabled === false) return false
    }
  } catch {
    // unreadable settings — default to enabled
  }
  return true
}

export function setPetEnabled(enabled: boolean): void {
  const settingsDir = petSettingsDir()
  const settingsPath = join(settingsDir, 'pet_settings.json')
  let settings: Record<string, unknown> = {}
  try {
    if (existsSync(settingsPath)) {
      const data = JSON.parse(readFileSync(settingsPath, 'utf-8'))
      if (data && typeof data === 'object') settings = data
    }
  } catch {
    // Replace unreadable settings with a valid file.
  }
  mkdirSync(settingsDir, { recursive: true })
  writeFileSync(settingsPath, JSON.stringify({ ...settings, enabled }, null, 2), 'utf-8')
}

function petSettingsDir(): string {
  return process.env.COLLIE_HOME?.trim() || join(homedir(), '.collie')
}

export function spawnPet(isDev: boolean): boolean {
  if (!PET_AVAILABLE) {
    console.log('[pet] Desktop pet is gated off for this release (coming soon).')
    return false
  }
  if (child) return true
  const python = findPython(isDev)
  if (!python) {
    console.error('[pet] Python not found; the collie stays in the kennel.')
    return false
  }

  stopping = false
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: '1' }
  delete env.PYTHONPATH
  delete env.PYTHONHOME
  delete env.VIRTUAL_ENV
  child = spawn(python, ['-m', 'collie_core.pet'], {
    cwd: coreRoot(isDev),
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })
  spawnedAt = Date.now()

  // A failed spawn must not take the Electron main process down.
  child.on('error', (error) => {
    console.error('[pet] spawn failed', error)
    child = null
  })

  child.stdout?.on('data', (d: Buffer) => {
    const text = d.toString().trim()
    if (text) console.log(`[pet] ${text}`)
  })
  child.stderr?.on('data', (d: Buffer) => {
    const text = d.toString().trim()
    if (text) console.log(`[pet] ${text}`)
  })

  child.on('exit', (code) => {
    console.log(`[pet] exited with code ${code}`)
    child = null
    // Clean exit (user right-clicked Quit) or deliberate stop: let it rest.
    if (stopping || code === 0) return
    // A long healthy run resets the crash budget; only rapid crash loops are
    // capped. The counter must NOT be reset on every spawn (that would make
    // the cap below dead code).
    if (Date.now() - spawnedAt > 60_000) crashRestarts = 0
    if (crashRestarts < 3) {
      crashRestarts += 1
      respawnTimer = setTimeout(() => {
        respawnTimer = null
        if (!child && !stopping) spawnPet(isDev)
      }, 2000)
    }
  })
  return true
}

export function stopPet(): void {
  stopping = true
  if (respawnTimer) {
    clearTimeout(respawnTimer)
    respawnTimer = null
  }
  if (child) {
    try {
      child.kill()
    } catch {
      // already gone
    }
    child = null
  }
}
