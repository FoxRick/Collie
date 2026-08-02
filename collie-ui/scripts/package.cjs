const { existsSync, readFileSync } = require('fs')
const { spawnSync } = require('child_process')
const { join, resolve } = require('path')
const { stagingPath } = require('./build-provenance.cjs')
const { writeManifest, verifyManifest } = require('./artifact-provenance.cjs')
const {
  moveReleaseArtifacts,
  prepareReleaseOutput,
  removeStagingOutput
} = require('./release-output.cjs')

const uiRoot = resolve(__dirname, '..')
const repositoryRoot = resolve(uiRoot, '..')
const npmCli = process.env.npm_execpath

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || uiRoot,
    stdio: 'inherit',
    windowsHide: true,
    ...options
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status || 1)
}

function readStagedProvenance() {
  if (!existsSync(stagingPath)) throw new Error(`Missing staged provenance: ${stagingPath}`)
  return JSON.parse(readFileSync(stagingPath, 'utf8'))
}

function runNpm(args) {
  // npm.cmd cannot be spawned directly by Node on Windows. npm_execpath is
  // supplied for every npm script and keeps this orchestrator shell-free.
  if (npmCli) return run(process.execPath, [npmCli, ...args])
  return run(process.platform === 'win32' ? 'npm.cmd' : 'npm', args, {
    shell: process.platform === 'win32'
  })
}

try {
  runNpm(['run', 'provenance:write'])
  const provenance = readStagedProvenance()
  const paths = prepareReleaseOutput(provenance)
  runNpm(['run', 'build'])
  runNpm(['run', 'stage:core'])
  runNpm(['exec', '--', 'electron-builder', `--config.directories.output=${paths.staging}`])
  moveReleaseArtifacts(paths, provenance)
  writeManifest(paths.release)
  verifyManifest(paths.release)
  const python = resolve(repositoryRoot, 'collie-core', '.venv', 'Scripts', 'python.exe')
  if (!existsSync(python)) throw new Error(`Release validator Python is missing: ${python}`)
  run(python, [resolve(repositoryRoot, 'tools', 'validate_release_artifacts.py'), paths.release])
  removeStagingOutput(paths)
  console.log(`[package] Release candidate ready: ${paths.release}`)
} catch (error) {
  console.error(`[package] ${error.message || error}`)
  process.exit(1)
}
