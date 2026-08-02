import { execFile } from 'child_process'
import { win32 } from 'path'
import { promisify } from 'util'

const execFileAsync = promisify(execFile)
const LOCAL_WINDOWS_DRIVE_TYPES = new Set([2, 3, 6])

export type WindowsDriveTypeLookup = (drive: string) => Promise<number | null>

function driveRoot(path: string): string | null {
  const root = win32.parse(path).root
  const match = /^([A-Za-z]:)\\$/.exec(root)
  return match?.[1] ?? null
}

async function lookupWindowsDriveType(drive: string): Promise<number | null> {
  try {
    const { stdout } = await execFileAsync(
      'powershell.exe',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `$disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='${drive}'" -ErrorAction Stop; [Console]::Out.Write($disk.DriveType)`
      ],
      { windowsHide: true }
    )
    const value = Number.parseInt(stdout.trim(), 10)
    return Number.isInteger(value) ? value : null
  } catch {
    return null
  }
}

/**
 * A drive-letter path can still be an SMB share mapped to a local letter.
 * Keep the desktop picker honest about its local-files-only promise and fail
 * closed when Windows cannot classify the backing volume.
 */
export async function assertLocalWindowsFileAccessFolder(
  canonicalPath: string,
  lookup: WindowsDriveTypeLookup = lookupWindowsDriveType,
  platform = process.platform
): Promise<void> {
  if (platform !== 'win32') return
  const drive = driveRoot(canonicalPath)
  if (!drive) throw new Error('Choose a folder stored on this computer.')
  const driveType = await lookup(drive)
  if (driveType === null || !LOCAL_WINDOWS_DRIVE_TYPES.has(driveType)) {
    throw new Error('Choose a folder stored on this computer.')
  }
}
