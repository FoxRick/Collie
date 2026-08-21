import { readFileSync } from 'fs'
import { describe, expect, it } from 'vitest'
import packageJson from '../../package.json'

describe('alpha updater publish configuration', () => {
  it('targets GitHub prereleases and the alpha metadata channel', () => {
    const config = readFileSync(new URL('../../electron-builder.yml', import.meta.url), 'utf8')

    // Version scheme: 0.1.0-alpha.<n> with an OPTIONAL patch level (alpha.7.1).
    // The patch level exists for the 2026-08-21 OTA update test (alpha.7 → alpha.7.1);
    // semver handles it fine (alpha.7.1 > alpha.7) and electron-updater compares
    // versions with semver, so the dotted form is safe for the alpha feed.
    expect(packageJson.version).toMatch(/-alpha\.\d+(\.\d+)*$/)
    expect(config).toMatch(
      /publish:\s+provider: github\s+owner: FoxRick\s+repo: Collie\s+channel: alpha\s+releaseType: prerelease/
    )
    const channel = config.match(/^\s+channel:\s+(\S+)$/m)?.[1]
    expect(`${channel}.yml`).toBe('alpha.yml')
  })
})
