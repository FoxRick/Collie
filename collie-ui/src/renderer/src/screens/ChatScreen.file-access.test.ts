// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { loadFileAccessScope, persistFileAccessScope } from './ChatScreen'

describe('ChatScreen file access preferences', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('never restores full file access from a previous session', () => {
    localStorage.setItem('collie.fileAccessScope', JSON.stringify({ mode: 'full_file_access' }))

    expect(loadFileAccessScope()).toEqual({ mode: 'selected_folder' })
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
