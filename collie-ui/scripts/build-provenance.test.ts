import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import packageJson from '../package.json'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const {
  assertCleanRepository,
  makeProvenance,
  packagedFileName,
  validateProvenance
} = require('./build-provenance.cjs') as {
  assertCleanRepository: (git: (args: string[]) => string) => void
  makeProvenance: (git: (args: string[]) => string, builtAt: string) => {
    productVersion: string
    gitSha: string
    dirty: boolean
    builtAt: string
  }
  packagedFileName: string
  validateProvenance: (value: unknown, expectedVersion?: string) => unknown
}

describe('build provenance', () => {
  it('rejects a worktree with tracked or untracked changes', () => {
    expect(() => assertCleanRepository(() => ' M collie-ui/package.json')).toThrow(/dirty Git worktree/)
    expect(() => assertCleanRepository(() => '?? collie-ui/new-file')).toThrow(/dirty Git worktree/)
  })

  it('records a clean immutable release identity', () => {
    const provenance = makeProvenance(
      (args) => args[0] === 'status' ? '' : 'C9028EC4D4DD84550C546DB717015B3E83DC3577',
      '2026-08-02T12:34:56.789Z'
    )

    expect(provenance).toMatchObject({
      productVersion: packageJson.version,
      gitSha: 'c9028ec4d4dd84550c546db717015b3e83dc3577',
      dirty: false,
      builtAt: '2026-08-02T12:34:56.789Z'
    })
  })

  it('rejects incomplete, mismatched, or dirty packaged provenance', () => {
    expect(() => validateProvenance({ dirty: false })).toThrow(/schema/)
    expect(() => validateProvenance({
      schemaVersion: 1,
      productVersion: '0.1.0-alpha.999',
      gitSha: 'c9028ec4d4dd84550c546db717015b3e83dc3577',
      dirty: false,
      builtAt: '2026-08-02T12:34:56.789Z'
    })).toThrow(/productVersion/)
    expect(() => validateProvenance({
      schemaVersion: 1,
      productVersion: packageJson.version,
      gitSha: 'c9028ec4d4dd84550c546db717015b3e83dc3577',
      dirty: true,
      builtAt: '2026-08-02T12:34:56.789Z'
    })).toThrow(/dirty/)
  })

  it('uses one stable packaged-resource file name', () => {
    expect(packagedFileName).toBe('collie-build-provenance.json')
  })

  it('requires provenance before packaging and ships it as an extra resource', () => {
    const packageScripts = packageJson.scripts
    const builderConfig = readFileSync(resolve('electron-builder.yml'), 'utf8')

    expect(packageScripts.dist).toBe('node scripts/package.cjs')
    expect(packageScripts.dist).not.toContain('electron-vite build')
    expect(builderConfig).toContain('beforePack: ./scripts/electron-builder-before-pack.cjs')
    expect(builderConfig).toContain('from: .electron-bundle/build-provenance.json')
    expect(builderConfig).toContain('to: collie-build-provenance.json')
  })
})
