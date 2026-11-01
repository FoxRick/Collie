import { beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ open: vi.fn(), save: vi.fn(), core: vi.fn() }))
vi.mock('electron', () => ({ dialog: { showOpenDialog: mocks.open, showSaveDialog: mocks.save } }))
vi.mock('./core-client', () => ({ commandWithCore: mocks.core }))
import { browserFileAction } from './browser-files'

const command = { operation: 'observe' as const, session_id: 's', ref: 'r', observation_id: 'o', text: 'ignored' }
beforeEach(() => vi.clearAllMocks())
describe('browser file selection', () => {
  it('passes only native-picked path and the observed target to core', async () => {
    mocks.open.mockResolvedValue({ canceled: false, filePaths: ['C:\\chosen.txt'] })
    mocks.core.mockResolvedValue({ message: 'ready' })
    await browserFileAction(null, 'chat', { ...command, file_path: 'forged' } as typeof command, 'upload')
    expect(mocks.core).toHaveBeenCalledWith('browser_action', { conversation_id: 'chat', command: {
      operation: 'upload', session_id: 's', ref: 'r', observation_id: 'o', file_path: 'C:\\chosen.txt'
    } })
  })
  it('does nothing when the user cancels or has no observed target', async () => {
    mocks.save.mockResolvedValue({ canceled: true })
    expect(await browserFileAction(null, 'chat', command, 'download')).toBeNull()
    expect(mocks.core).not.toHaveBeenCalled()
    await expect(browserFileAction(null, 'chat', { operation: 'observe' }, 'upload')).rejects.toThrow('Read the browser')
    expect(mocks.open).not.toHaveBeenCalled()
  })
})
