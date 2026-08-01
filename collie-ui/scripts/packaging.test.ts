import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const {
  containsRepositoryPath,
  isNonPortableSitePackageArtifact,
  repositoryPathVariants
} = require('./packaging-portability.cjs') as {
  containsRepositoryPath: (contents: string | Buffer, repositoryRoot: string) => boolean
  isNonPortableSitePackageArtifact: (path: string, contents?: string) => boolean
  repositoryPathVariants: (repositoryRoot: string) => string[]
}

describe('packaging portability', () => {
  it('filters editable installs and local-install metadata', () => {
    expect(isNonPortableSitePackageArtifact(
      '_editable_impl_collie_core.pth',
      'C:\\work\\collie-core\n'
    )).toBe(true)
    expect(isNonPortableSitePackageArtifact(
      'collie_core.dist-info/direct_url.json',
      '{}'
    )).toBe(true)
    expect(isNonPortableSitePackageArtifact('collie_core.egg-link', '../collie-core')).toBe(true)
  })

  it('filters absolute .pth paths while preserving relative runtime package paths', () => {
    expect(isNonPortableSitePackageArtifact('absolute.pth', 'D:\\src\\package\n')).toBe(true)
    expect(isNonPortableSitePackageArtifact('unix.pth', '/home/dev/package\n')).toBe(true)
    expect(isNonPortableSitePackageArtifact('pywin32.pth', 'win32\nwin32\\lib\nPythonwin\n')).toBe(false)
    expect(isNonPortableSitePackageArtifact(
      'runtime-hook.pth',
      'import sys; sys.example = True\n'
    )).toBe(false)
  })

  it('recognizes raw, slash-normalized, JSON-escaped, and file-URL repository paths', () => {
    const repositoryRoot = resolve('C:\\Users\\developer\\Collie')
    const variants = repositoryPathVariants(repositoryRoot)

    for (const variant of variants) {
      expect(containsRepositoryPath(`prefix ${variant} suffix`, repositoryRoot)).toBe(true)
    }
    expect(containsRepositoryPath('portable relative/path only', repositoryRoot)).toBe(false)
  })

  it('keeps staging probes and packaged smoke from writing path-bearing bytecode', () => {
    const stageSource = readFileSync(resolve('scripts/stage-core.cjs'), 'utf8')
    const smokeSource = readFileSync(resolve('scripts/smoke-packaged-core.cjs'), 'utf8')

    expect(stageSource.match(/PYTHONDONTWRITEBYTECODE/g)).toHaveLength(2)
    expect(smokeSource).toContain("PYTHONDONTWRITEBYTECODE: '1'")
    expect(smokeSource).toContain("server.listen(0, '127.0.0.1'")
    expect(smokeSource).toContain("payload.port !== requestedPort")
    expect(stageSource.lastIndexOf('verifyPortableBundle()')).toBeGreaterThan(
      stageSource.indexOf('process.stdout.write(mcpProbe.stdout)')
    )
  })
})
