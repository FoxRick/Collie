import { mkdtempSync, readFileSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  emptyUpdateBootRecord,
  evaluateUpdateBootRecord,
  readUpdateBootRecord,
  recordPendingUpdate,
  writeUpdateBootRecord,
  type UpdateBootRecord
} from './update-boot-record'

let dir: string
let path: string

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'collie-boot-record-'))
  path = join(dir, 'update-boot-record.json')
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
})

describe('read/writeUpdateBootRecord', () => {
  it('round-trips a record through disk', () => {
    const record: UpdateBootRecord = {
      pendingVersion: '0.1.0-alpha.5',
      previousVersion: '0.1.0-alpha.4',
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: '2026-08-08T00:00:00.000Z'
    }
    writeUpdateBootRecord(path, record)
    expect(readUpdateBootRecord(path)).toEqual(record)
  })

  it('returns null for a missing or corrupt file', () => {
    expect(readUpdateBootRecord(join(dir, 'nope.json'))).toBeNull()
    writeUpdateBootRecord(path, emptyUpdateBootRecord())
    expect(readUpdateBootRecord(path)).not.toBeNull()
  })

  it('tolerates malformed fields', () => {
    writeUpdateBootRecord(path, {
      pendingVersion: '',
      previousVersion: 42 as unknown as string,
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: 42 as unknown as string
    })
    const record = readUpdateBootRecord(path)
    expect(record).toEqual({
      pendingVersion: null,
      previousVersion: null,
      lastGoodVersion: '0.1.0-alpha.4',
      updatedAt: null
    })
  })
})

describe('recordPendingUpdate', () => {
  it('keeps the last-known-good version and stamps the change', () => {
    const before = { ...emptyUpdateBootRecord(), lastGoodVersion: '0.1.0-alpha.4' }
    const next = recordPendingUpdate(before, '0.1.0-alpha.4', '0.1.0-alpha.5')
    expect(next.pendingVersion).toBe('0.1.0-alpha.5')
    expect(next.previousVersion).toBe('0.1.0-alpha.4')
    expect(next.lastGoodVersion).toBe('0.1.0-alpha.4')
    expect(next.updatedAt).not.toBeNull()
  })
})

describe('evaluateUpdateBootRecord', () => {
  const record: UpdateBootRecord = {
    pendingVersion: '0.1.0-alpha.5',
    previousVersion: '0.1.0-alpha.4',
    lastGoodVersion: '0.1.0-alpha.4',
    updatedAt: '2026-08-08T00:00:00.000Z'
  }

  it('accepts the update when the pending version boots healthy', () => {
    const { evaluation, next } = evaluateUpdateBootRecord(record, '0.1.0-alpha.5', true)
    expect(evaluation).toEqual({ kind: 'accepted', previousVersion: '0.1.0-alpha.4' })
    expect(next.pendingVersion).toBeNull()
    expect(next.previousVersion).toBeNull()
    expect(next.lastGoodVersion).toBe('0.1.0-alpha.5')
  })

  it('flags rollback-needed when the pending version boots with a failed core', () => {
    const { evaluation, next } = evaluateUpdateBootRecord(record, '0.1.0-alpha.5', false)
    expect(evaluation).toEqual({ kind: 'rollback-needed', previousVersion: '0.1.0-alpha.4' })
    expect(next).toEqual(record)
  })

  it('is a noop when no pending install matches the running version', () => {
    const { evaluation, next } = evaluateUpdateBootRecord(record, '0.1.0-alpha.4', true)
    expect(evaluation.kind).toBe('noop')
    expect(next).toEqual(record)
  })
})
