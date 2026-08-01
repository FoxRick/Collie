import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { inspectDevPythonEnvironment } from './python-environment'

describe('development Python environment inspection', () => {
  const root = join('C:', 'repo')
  const venv = join(root, 'collie-core', '.venv')
  const python = join(venv, 'Scripts', 'python.exe')
  const config = join(venv, 'pyvenv.cfg')
  const sitePackages = join(venv, 'Lib', 'site-packages')

  it('accepts a complete Windows venv', () => {
    const paths = new Set([python, config])
    expect(inspectDevPythonEnvironment(root, 'win32', (path) => paths.has(path))).toEqual({
      python,
      error: ''
    })
  })

  it('explains how to repair a partial Windows venv', () => {
    const paths = new Set([sitePackages])
    const result = inspectDevPythonEnvironment(root, 'win32', (path) => paths.has(path))
    expect(result.python).toBeNull()
    expect(result.error).toContain('site-packages exists')
    expect(result.error).toContain('Python 3.12 -m venv')
  })
})
