import { existsSync } from 'fs'
import { join } from 'path'

type Exists = (path: string) => boolean

export interface DevPythonEnvironment {
  python: string | null
  error: string
}

export function inspectDevPythonEnvironment(
  repoRoot: string,
  platform: NodeJS.Platform = process.platform,
  exists: Exists = existsSync
): DevPythonEnvironment {
  const venv = join(repoRoot, 'collie-core', '.venv')
  const config = join(venv, 'pyvenv.cfg')
  const python =
    platform === 'win32' ? join(venv, 'Scripts', 'python.exe') : join(venv, 'bin', 'python')
  const sitePackages =
    platform === 'win32' ? join(venv, 'Lib', 'site-packages') : join(venv, 'lib')

  if (exists(python) && exists(config)) return { python, error: '' }

  if (exists(sitePackages) && (!exists(python) || !exists(config))) {
    return {
      python: null,
      error:
        'The collie-core/.venv environment is incomplete: site-packages exists, but the ' +
        'Python executable or pyvenv.cfg is missing. Recreate it with Python 3.12 -m venv.'
    }
  }

  return {
    python: null,
    error:
      'Python core not found. Create collie-core/.venv with Python 3.12 and install collie-core[dev].'
  }
}
