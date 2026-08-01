import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const scriptsDir = dirname(fileURLToPath(import.meta.url))
const uiRoot = resolve(scriptsDir, '..')
const repositoryRoot = resolve(uiRoot, '..')

function read(path: string): string {
  return readFileSync(path, 'utf8').replace(/\r\n/g, '\n')
}

describe('portable development launcher', () => {
  it('starts from its own directory and explains a missing npm.cmd', () => {
    const launcher = read(resolve(repositoryRoot, 'start-collie.bat'))

    expect(launcher).toContain('cd /d "%~dp0collie-ui"')
    expect(launcher).toContain('call npm.cmd run dev')
    expect(launcher).toMatch(/where npm\.cmd >nul 2>&1/i)
    expect(launcher).toMatch(/npm\.cmd was not found/i)
    expect(launcher).toMatch(/Install Node\.js/i)
    expect(launcher).not.toMatch(/[A-Z]:\\Users\\/i)
    expect(launcher).not.toContain('call npm run dev')
  })
})

describe('verification diagnostics', () => {
  const verificationScripts = [
    'verify-running-electron.cjs',
    'verify-ui-ux.cjs'
  ]

  it.each(verificationScripts)('%s strips core.token inside the renderer', (name) => {
    const source = read(resolve(scriptsDir, name))

    expect(source).toMatch(
      /const \{ token(?:: [_A-Za-z][_A-Za-z0-9]*)?, \.\.\.safeCore \} = await window\.collie\.coreState\(\)/
    )
    expect(source).not.toMatch(
      /evaluate\(\s*['"]window\.collie\.coreState\(\)['"]\s*\)/
    )
  })

  it('authenticates the direct UI/UX core probe without returning the token', () => {
    const source = read(resolve(scriptsDir, 'verify-ui-ux.cjs'))

    expect(source).toMatch(/new WebSocket\([\s\S]*?'collie-' \+ token[\s\S]*?\)/)
    expect(source).toContain('return { core: safeCore, ipcProbe }')
    expect(source).not.toMatch(/return \{[^}]*token[^}]*ipcProbe/)
  })

  it('keeps every script that reads coreState on the token-stripping path', () => {
    const readers = readdirSync(scriptsDir)
      .filter((name) => name.endsWith('.cjs'))
      .map((name) => ({ name, source: read(resolve(scriptsDir, name)) }))
      .filter(({ source }) => source.includes('window.collie.coreState()'))

    expect(readers.map(({ name }) => name).sort()).toEqual(verificationScripts.slice().sort())
    for (const { source } of readers) {
      expect(source).toContain('...safeCore')
      expect(source).not.toMatch(
        /evaluate\(\s*['"]window\.collie\.coreState\(\)['"]\s*\)/
      )
    }
  })
})
