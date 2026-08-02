// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  FULL_FILE_ACCESS_CONFIRMATION,
  confirmFileAccessScopeChange,
  loadFileAccessScope,
  persistFileAccessScope
} from './ChatScreen'

describe('ChatScreen file access preferences', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('never restores full file access from a previous session', () => {
    localStorage.setItem('collie.fileAccessScope', JSON.stringify({ mode: 'full_file_access' }))

    expect(loadFileAccessScope()).toEqual({ mode: 'selected_folder' })
  })

  it('asks for deliberate confirmation before enabling full file access', () => {
    const confirmFullAccess = vi.fn().mockReturnValue(false)

    expect(
      confirmFileAccessScopeChange({ mode: 'full_file_access' }, confirmFullAccess)
    ).toBe(false)
    expect(confirmFullAccess).toHaveBeenCalledWith(FULL_FILE_ACCESS_CONFIRMATION)
    expect(FULL_FILE_ACCESS_CONFIRMATION).toContain(
      'read and change local text files anywhere on this computer'
    )
    expect(FULL_FILE_ACCESS_CONFIRMATION).toContain(
      'cannot send, delete, pay, publish, change accounts, or change routines'
    )
  })

  it('keeps full file access session-only while persisting narrower scopes', () => {
    localStorage.setItem(
      'collie.fileAccessScope',
      JSON.stringify({ mode: 'chosen_folders', roots: ['C:\\workspace'] })
    )

    persistFileAccessScope({ mode: 'full_file_access' })
    expect(JSON.parse(localStorage.getItem('collie.fileAccessScope') || '{}')).toEqual({
      mode: 'chosen_folders',
      roots: ['C:\\workspace']
    })

    persistFileAccessScope({ mode: 'chosen_folders', roots: ['C:\\workspace'] })
    expect(JSON.parse(localStorage.getItem('collie.fileAccessScope') || '{}')).toEqual({
      mode: 'chosen_folders',
      roots: ['C:\\workspace']
    })
  })
})
