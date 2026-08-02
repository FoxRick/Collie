import { describe, expect, it, vi } from 'vitest'
import { assertLocalWindowsFileAccessFolder } from './local-file-access'

describe('local file access folder validation', () => {
  it('allows local, removable, and RAM-drive folders', async () => {
    for (const driveType of [2, 3, 6]) {
      const lookup = vi.fn().mockResolvedValue(driveType)
      await expect(
        assertLocalWindowsFileAccessFolder('C:\\Users\\Collie', lookup, 'win32')
      ).resolves.toBeUndefined()
      expect(lookup).toHaveBeenCalledWith('C:')
    }
  })

  it('rejects mapped network drives and an unclassified volume', async () => {
    await expect(
      assertLocalWindowsFileAccessFolder('Z:\\Shared', vi.fn().mockResolvedValue(4), 'win32')
    ).rejects.toThrow('stored on this computer')
    await expect(
      assertLocalWindowsFileAccessFolder('Z:\\Shared', vi.fn().mockResolvedValue(null), 'win32')
    ).rejects.toThrow('stored on this computer')
  })
})
