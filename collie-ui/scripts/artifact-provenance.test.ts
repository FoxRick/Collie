import { createRequire } from 'node:module'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import packageJson from '../package.json'

const require = createRequire(import.meta.url)
const {
  assertReleaseDirectory,
  payloadNames,
  releaseFiles,
  verifyManifest,
  writeManifest
} = require('./artifact-provenance.cjs') as {
  assertReleaseDirectory: (root: string, version: string, options?: { complete?: boolean }) => void
  payloadNames: (version: string) => string[]
  releaseFiles: (root: string, version: string) => Array<{ file: string; bytes: number; sha256: string }>
  verifyManifest: (root: string) => unknown
  writeManifest: (root: string) => unknown
}

const version = packageJson.version

function writePayload(root: string): void {
  const [installer, blockmap, metadata, provenance] = payloadNames(version)
  writeFileSync(join(root, installer), 'installer')
  writeFileSync(join(root, blockmap), 'blockmap')
  writeFileSync(join(root, metadata), 'path: Collie-Setup.exe')
  writeFileSync(join(root, provenance), '{}')
}

describe('artifact provenance', () => {
  it('lists only the strict release payload with stable SHA-256 digests', () => {
    const root = mkdtempSync(join(tmpdir(), 'collie-artifact-provenance-'))
    try {
      writePayload(root)

      expect(releaseFiles(root, version)).toEqual([
        {
          file: `Collie-Setup-${version}.exe`,
          bytes: 9,
          sha256: '9c0d294c05fc1d88d698034609bb81c0c69196327594e4c69d2915c80fd9850c'
        },
        {
          file: `Collie-Setup-${version}.exe.blockmap`,
          bytes: 8,
          sha256: '09bc5d492c0a86ac994b1553c3519ef8717b512962c622db47482120ebe0797b'
        },
        {
          file: 'alpha.yml',
          bytes: 22,
          sha256: 'd51952bb291b691a7fd9e1b445c63f5ff1ca85c4f82359f963e7b20119d54ec7'
        },
        {
          file: 'collie-build-provenance.json',
          bytes: 2,
          sha256: '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'
        }
      ])
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('rejects stale root files and directories rather than hashing them', () => {
    const root = mkdtempSync(join(tmpdir(), 'collie-artifact-provenance-'))
    try {
      writePayload(root)
      writeFileSync(join(root, 'old-installer.exe'), 'stale')
      mkdirSync(join(root, 'win-unpacked'))

      expect(() => assertReleaseDirectory(root, version)).toThrow(
        /Unexpected release output: old-installer\.exe, win-unpacked/
      )
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('regenerates SHA256SUMS from the strict release allowlist', () => {
    const root = mkdtempSync(join(tmpdir(), 'collie-artifact-provenance-'))
    try {
      writePayload(root)
      writeFileSync(join(root, 'collie-build-provenance.json'), JSON.stringify({
        schemaVersion: 1,
        productVersion: packageJson.version,
        gitSha: 'c9028ec4d4dd84550c546db717015b3e83dc3577',
        dirty: false,
        builtAt: '2026-08-02T12:34:56.789Z'
      }))

      writeManifest(root)
      expect(verifyManifest(root)).toMatchObject({ productVersion: packageJson.version })
      expect(readFileSync(join(root, 'SHA256SUMS.txt'), 'utf8')).toContain(
        'collie-artifact-provenance.json'
      )
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
