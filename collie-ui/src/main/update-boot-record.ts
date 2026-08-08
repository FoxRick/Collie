/**
 * Durable "did the last update actually come up healthy?" record.
 *
 * Written before an install starts (pending version + previous version),
 * re-read on the next boot: if the installed version matches the pending
 * one AND the Python core reaches `running`, the update is accepted and the
 * record clears. If the core fails, the previous version stays the
 * last-known-good one and the UI surfaces a failed-update recovery notice —
 * this is detection, not an automatic rollback: nothing is reinstalled or
 * restored, the user just learns the new version is unreliable.
 *
 * The record is deliberately small and tolerant: a missing or corrupt file
 * reads back as null and the app simply behaves as a normal first boot.
 */
import { readFileSync, renameSync, writeFileSync } from 'fs'

export interface UpdateBootRecord {
  /** Version that was about to be installed when the app quit. */
  pendingVersion: string | null
  /** Version the app was running before the pending install. */
  previousVersion: string | null
  /** Last version whose core booted healthy. */
  lastGoodVersion: string | null
  /** ISO timestamp of the last record change. */
  updatedAt: string | null
}

export function emptyUpdateBootRecord(): UpdateBootRecord {
  return {
    pendingVersion: null,
    previousVersion: null,
    lastGoodVersion: null,
    updatedAt: null
  }
}

function pickString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

export function readUpdateBootRecord(path: string): UpdateBootRecord | null {
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>
    if (typeof parsed !== 'object' || parsed === null) return null
    return {
      pendingVersion: pickString(parsed.pendingVersion),
      previousVersion: pickString(parsed.previousVersion),
      lastGoodVersion: pickString(parsed.lastGoodVersion),
      updatedAt: pickString(parsed.updatedAt)
    }
  } catch {
    return null
  }
}

export function writeUpdateBootRecord(path: string, record: UpdateBootRecord): void {
  // Write-then-rename keeps the record atomic for a crash mid-write.
  const tmp = `${path}.tmp`
  writeFileSync(tmp, JSON.stringify(record, null, 2), 'utf8')
  renameSync(tmp, path)
}

export function recordPendingUpdate(
  current: UpdateBootRecord,
  previousVersion: string,
  pendingVersion: string
): UpdateBootRecord {
  return {
    pendingVersion,
    previousVersion,
    lastGoodVersion: current.lastGoodVersion,
    updatedAt: new Date().toISOString()
  }
}

export type BootEvaluation =
  | { kind: 'noop' }
  | { kind: 'accepted'; previousVersion: string | null }
  | { kind: 'rollback-needed'; previousVersion: string | null }

/**
 * Evaluate a boot record against the version that actually started.
 * - pendingVersion matches currentVersion and the core is healthy → accepted,
 *   record clears and the current version becomes last-known-good.
 * - pendingVersion matches but the core failed → rollback-needed (failed-update
 *   detection); the record is kept so the UI can keep showing the recovery
 *   notice until the user dismisses it or a later boot is accepted.
 * - anything else → noop (first boot, no pending install, or a manual
 *   reinstall of an old version — nothing to verify).
 */
export function evaluateUpdateBootRecord(
  record: UpdateBootRecord,
  currentVersion: string,
  coreHealthy: boolean
): { evaluation: BootEvaluation; next: UpdateBootRecord } {
  if (record.pendingVersion === currentVersion) {
    if (coreHealthy) {
      return {
        evaluation: { kind: 'accepted', previousVersion: record.previousVersion },
        next: {
          pendingVersion: null,
          previousVersion: null,
          lastGoodVersion: currentVersion,
          updatedAt: new Date().toISOString()
        }
      }
    }
    return {
      evaluation: { kind: 'rollback-needed', previousVersion: record.previousVersion },
      next: record
    }
  }
  return { evaluation: { kind: 'noop' }, next: record }
}
