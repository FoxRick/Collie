import { describe, expect, it } from 'vitest'
import { canComposeSharedSession, canInviteToSession, formatBytes, sharedMessages } from './collaboration'

describe('collaboration display helpers', () => {
  it('formats quota bytes with stable binary units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.0 KiB')
    expect(formatBytes(2 * 1024 * 1024)).toBe('2.0 MiB')
  })

  it('materializes edits and deletions while retaining unsynced local content', () => {
    const rows = sharedMessages([
      { event_id: 'e1', message_id: 'm1', kind: 'message', seq: 1, content: 'first' },
      { event_id: 'e2', message_id: 'm2', kind: 'message', seq: 2, content: 'remove me' },
      { event_id: 'e3', message_id: 'm1', kind: 'edit', seq: 3, content: 'edited' },
      { event_id: 'e4', message_id: 'm2', kind: 'delete', seq: 4 },
      { event_id: 'pending', message_id: 'm3', kind: 'message', content: 'offline' }
    ])
    expect(rows.map(row => [row.id, row.content, row.sequence, row.sync_status])).toEqual([
      ['m1', 'edited', 1, 'synced'], ['m3', 'offline', 0, 'pending']
    ])
  })

  it('closes composition for archive and purge states', () => {
    expect(canComposeSharedSession('active')).toBe(true)
    for (const status of ['closing', 'archive_pending', 'purging', 'archived_local', 'unknown', undefined]) {
      expect(canComposeSharedSession(status)).toBe(false)
    }
  })

  it('does not treat directory membership as access and rejects duplicate invites', () => {
    expect(canInviteToSession(undefined, 'member-2', [{ id: 'member-1' }])).toBe(false)
    expect(canInviteToSession('session-1', 'member-1', [{ id: 'member-1' }])).toBe(false)
    expect(canInviteToSession('session-1', 'member-2', [{ id: 'member-1' }])).toBe(true)
  })
})
