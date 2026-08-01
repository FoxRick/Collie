import { readFileSync } from 'fs'
import { describe, expect, it } from 'vitest'
import packageJson from '../../package.json'

describe('alpha updater publish configuration', () => {
  it('targets GitHub prereleases and the alpha metadata channel', () => {
    const config = readFileSync(new URL('../../electron-builder.yml', import.meta.url), 'utf8')

    expect(packageJson.version).toMatch(/-alpha\.\d+$/)
    expect(config).toMatch(
      /publish:\s+provider: github\s+owner: FoxRick\s+repo: Collie\s+channel: alpha\s+releaseType: prerelease/
    )
    const channel = config.match(/^\s+channel:\s+(\S+)$/m)?.[1]
    expect(`${channel}.yml`).toBe('alpha.yml')
  })
})
