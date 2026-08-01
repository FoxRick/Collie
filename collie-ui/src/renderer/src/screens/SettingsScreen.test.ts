import { describe, expect, it } from 'vitest'
import { clearAllDataNotice } from './SettingsScreen'

describe('clear-all result reporting', () => {
  it('reports a complete clear only when every phase succeeded', () => {
    expect(clearAllDataNotice({
      cleared: true,
      partial: false,
      database_cleared: true,
      filesystem_cleared: true,
      warnings: []
    })).toContain('All clear')
  })

  it('reports filesystem warnings as a partial clear', () => {
    const notice = clearAllDataNotice({
      cleared: false,
      partial: true,
      database_cleared: true,
      filesystem_cleared: false,
      warnings: [
        {
          scope: 'filesystem',
          target: 'pairing.json',
          error: 'PermissionError: file is locked'
        }
      ]
    })

    expect(notice).toContain("cleared Collie's saved records")
    expect(notice).toContain("couldn't remove 1 local file or folder")
    expect(notice).not.toContain('All clear')
  })

  it('explains that files were preserved when the database clear failed', () => {
    const notice = clearAllDataNotice({
      cleared: false,
      partial: false,
      database_cleared: false,
      filesystem_cleared: false,
      warnings: [
        {
          scope: 'database',
          target: 'collie.db',
          error: 'RuntimeError: database is busy'
        }
      ]
    })

    expect(notice).toContain("couldn't clear Collie's database")
    expect(notice).toContain('left local files in place')
    expect(notice).not.toContain('All clear')
  })
})
