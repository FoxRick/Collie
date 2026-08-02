import { createRequire } from 'node:module'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const require = createRequire(import.meta.url)
const { assertBuilderOutput, prepareReleaseOutput, releaseSlug } = require('./release-output.cjs') as {
  assertBuilderOutput: (staging: string, version: string) => void
  prepareReleaseOutput: (provenance: { productVersion: string; gitSha: string }, root: string) => {
    staging: string
    release: string
  }
  releaseSlug: (provenance: { productVersion: string; gitSha: string }) => string
}

const provenance = {
  productVersion: '0.1.0-alpha.1',
  gitSha: 'c9028ec4d4dd84550c546db717015b3e83dc3577'
}

describe('release output', () => {
  it('uses a version-and-SHA-specific clean output pair', () => {
    const root = mkdtempSync(join(tmpdir(), 'collie-release-output-'))
    try {
      const initial = prepareReleaseOutput(provenance, root)
      writeFileSync(join(initial.staging, 'stale-builder-output'), 'stale')
      writeFileSync(join(initial.release, 'stale-release-output'), 'stale')
      const reset = prepareReleaseOutput(provenance, root)

      expect(releaseSlug(provenance)).toBe('0.1.0-alpha.1-c9028ec4d4dd')
      expect(reset).toEqual(initial)
      expect(existsSync(join(reset.staging, 'stale-builder-output'))).toBe(false)
      expect(existsSync(join(reset.release, 'stale-release-output'))).toBe(false)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('allows builder diagnostics only in staging and still rejects unknown output', () => {
    const root = mkdtempSync(join(tmpdir(), 'collie-release-output-'))
    try {
      const { staging } = prepareReleaseOutput(provenance, root)
      const installer = `Collie-Setup-${provenance.productVersion}.exe`
      writeFileSync(join(staging, installer), 'installer')
      writeFileSync(join(staging, `${installer}.blockmap`), 'blockmap')
      writeFileSync(join(staging, 'alpha.yml'), 'metadata')
      writeFileSync(join(staging, 'builder-debug.yml'), 'diagnostic')
      const resources = join(staging, 'win-unpacked', 'resources')
      mkdirSync(resources, { recursive: true })
      writeFileSync(join(resources, 'collie-build-provenance.json'), '{}')

      expect(() => assertBuilderOutput(staging, provenance.productVersion)).not.toThrow()
      writeFileSync(join(staging, 'stale-installer.exe'), 'stale')
      expect(() => assertBuilderOutput(staging, provenance.productVersion)).toThrow(
        /Unexpected electron-builder output: stale-installer\.exe/
      )
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
