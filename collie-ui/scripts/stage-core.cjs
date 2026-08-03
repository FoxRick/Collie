const { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } = require('fs')
const { dirname, join, relative, resolve, sep } = require('path')
const { spawnSync } = require('child_process')
const {
  filterPortableSitePackage,
  findPortabilityLeaks
} = require('./packaging-portability.cjs')

const uiRoot = resolve(__dirname, '..')
const repositoryRoot = resolve(uiRoot, '..')
const coreRoot = resolve(uiRoot, '..', 'collie-core')
const stageRoot = resolve(uiRoot, '.electron-bundle', 'collie-core')
const stageParent = resolve(uiRoot, '.electron-bundle')

function fail(message) {
  console.error(`[stage-core] ${message}`)
  process.exit(1)
}

function safeResetStage() {
  const rel = relative(uiRoot, stageRoot)
  if (!rel || rel.startsWith(`..${sep}`) || rel === '..') {
    fail(`Refusing to reset staging path outside the UI repository: ${stageRoot}`)
  }
  rmSync(stageRoot, { recursive: true, force: true })
  mkdirSync(stageRoot, { recursive: true })
}

function pythonHomeFromVenv() {
  const configured = process.env.COLLIE_PYTHON_HOME
  if (configured) return resolve(configured)

  const configPath = join(coreRoot, '.venv', 'pyvenv.cfg')
  if (!existsSync(configPath)) {
    fail(`Missing ${configPath}. Set COLLIE_PYTHON_HOME to a full Python 3.12 installation.`)
  }
  const match = readFileSync(configPath, 'utf8').match(/^home\s*=\s*(.+)$/m)
  if (!match) fail(`Could not read the Python home from ${configPath}.`)
  const venvHome = resolve(match[1].trim())
  if (existsSync(join(venvHome, 'python.exe'))) return venvHome

  // A previous verified stage is a valid build-runtime source when the local
  // system Python was removed. Fresh CI builds should set COLLIE_PYTHON_HOME.
  const previousStage = join(stageRoot, 'python')
  if (existsSync(join(previousStage, 'python.exe'))) {
    const cached = join(stageParent, '.python-build-runtime')
    rmSync(cached, { recursive: true, force: true })
    copyTree(previousStage, cached)
    return cached
  }
  fail(
    `Python runtime not found at ${venvHome}. Set COLLIE_PYTHON_HOME to a full Python 3.12 installation.`
  )
}

function copyTree(source, destination, filter) {
  if (!existsSync(source)) fail(`Required packaging source is missing: ${source}`)
  cpSync(source, destination, {
    recursive: true,
    force: true,
    filter: (currentSource) => {
      const rel = relative(source, currentSource)
      return filter ? filter(rel, currentSource) : true
    }
  })
}

function isCacheOrBytecode(rel) {
  const normalized = rel.replaceAll('\\', '/')
  return (
    normalized.split('/').includes('__pycache__') ||
    normalized.endsWith('.pyc') ||
    normalized.endsWith('.pyo')
  )
}

function stageSource() {
  copyTree(join(coreRoot, 'collie_core'), join(stageRoot, 'collie_core'), (rel) => {
    if (!rel) return true
    if (isCacheOrBytecode(rel)) return false
    return statSync(join(coreRoot, 'collie_core', rel)).isDirectory() ||
      /\.(py|png|webp|json|md)$/i.test(rel)
  })
  copyTree(join(coreRoot, 'nanobot'), join(stageRoot, 'nanobot'), (rel) => {
    if (!rel) return true
    if (isCacheOrBytecode(rel)) return false
    const normalized = rel.replaceAll('\\', '/')
    return statSync(join(coreRoot, 'nanobot', rel)).isDirectory() ||
      normalized.startsWith('templates/') ||
      normalized.startsWith('skills/') ||
      /\.(py|json|md)$/i.test(normalized)
  })
  cpSync(join(coreRoot, 'pyproject.toml'), join(stageRoot, 'pyproject.toml'))
}

function venvSitePackages() {
  const windows = join(coreRoot, '.venv', 'Lib', 'site-packages')
  if (existsSync(windows)) return windows
  const unixLib = join(coreRoot, '.venv', 'lib')
  if (existsSync(unixLib)) {
    for (const entry of readdirSync(unixLib, { withFileTypes: true })) {
      if (!entry.isDirectory() || !/^python3\.\d+$/.test(entry.name)) continue
      const candidate = join(unixLib, entry.name, 'site-packages')
      if (existsSync(candidate)) return candidate
    }
  }
  fail(`venv site-packages not found under ${coreRoot}/.venv`)
}

function stagePythonWindows(pythonHome) {
  const pythonExe = join(pythonHome, 'python.exe')
  if (!existsSync(pythonExe)) {
    fail(`Python runtime not found at ${pythonExe}. Set COLLIE_PYTHON_HOME correctly.`)
  }

  const destination = join(stageRoot, 'python')
  mkdirSync(destination, { recursive: true })

  for (const entry of [
    'python.exe',
    'pythonw.exe',
    'python3.dll',
    'python312.dll',
    'vcruntime140.dll',
    'vcruntime140_1.dll',
    'LICENSE.txt'
  ]) {
    const source = join(pythonHome, entry)
    if (existsSync(source)) cpSync(source, join(destination, entry), { force: true })
  }

  for (const directory of ['DLLs', 'Lib', 'tcl']) {
    const source = join(pythonHome, directory)
    if (!existsSync(source)) continue
    copyTree(source, join(destination, directory), (rel) => {
      if (!rel) return true
      const normalized = rel.replaceAll('\\', '/')
      if (directory === 'Lib' && (
        normalized === 'site-packages' ||
        normalized.startsWith('site-packages/')
      )) return false
      return !isCacheOrBytecode(normalized)
    })
  }

  copyVenvSitePackages(join(destination, 'Lib', 'site-packages'))
}

function isSymbolicLink(path) {
  try {
    return lstatSync(path).isSymbolicLink()
  } catch {
    return false
  }
}

function stagePythonLinux(pythonHome) {
  // python-build-standalone install layout:
  //   bin/python3(.12), lib/python3.12/…, lib/libpython3.12.so.1.0
  const launcher = join(pythonHome, 'bin', 'python3')
  if (!existsSync(launcher)) {
    fail(
      `Python runtime not found at ${launcher}. Set COLLIE_PYTHON_HOME to a ` +
        'python-build-standalone install directory (contains bin/python3 and lib/python3.x).'
    )
  }
  const stdlib = readdirSync(join(pythonHome, 'lib'))
    .find((entry) => /^python3\.\d+$/.test(entry))
  if (!stdlib) fail(`No python3.x stdlib directory under ${join(pythonHome, 'lib')}.`)

  const destination = join(stageRoot, 'python')
  mkdirSync(destination, { recursive: true })

  // Skip symlinks: python-build-standalone ships absolute aliases (python3,
  // pip3, idle3, libpython3.11.so → the build machine's paths). Only regular
  // files are portable; the python3 launcher is recreated below.
  copyTree(join(pythonHome, 'bin'), join(destination, 'bin'), (rel, source) => {
    if (!rel) return true
    if (isCacheOrBytecode(rel)) return false
    return !isSymbolicLink(source)
  })
  copyTree(join(pythonHome, 'lib', stdlib), join(destination, 'lib', stdlib), (rel, source) => {
    if (!rel) return true
    if (isSymbolicLink(source)) return false
    const normalized = rel.replaceAll('\\', '/')
    if (normalized === 'site-packages' || normalized.startsWith('site-packages/')) return false
    return !isCacheOrBytecode(normalized)
  })
  for (const entry of readdirSync(join(pythonHome, 'lib'))) {
    if (!entry.startsWith('libpython')) continue
    const source = join(pythonHome, 'lib', entry)
    if (isSymbolicLink(source)) continue
    cpSync(source, join(destination, 'lib', entry), { force: true })
  }

  copyVenvSitePackages(join(destination, 'lib', stdlib, 'site-packages'))

  // electron-builder beforePack + the Electron main resolve the launcher as
  // <resources>/collie-core/python/bin/python3 — make sure it exists.
  const stagedBin = join(destination, 'bin')
  if (!existsSync(join(stagedBin, 'python3'))) {
    const candidates = readdirSync(stagedBin)
      .filter((name) => /^python3(\.\d+)?$/.test(name))
      .sort()
    const launcherBinary = candidates[candidates.length - 1]
    if (!launcherBinary) fail(`No python3 launcher staged in ${stagedBin}.`)
    cpSync(join(stagedBin, launcherBinary), join(stagedBin, 'python3'), { force: true })
  }
}

function copyVenvSitePackages(destination) {
  const venvPackages = venvSitePackages()
  copyTree(venvPackages, destination, (rel) => {
    if (!rel) return true
    if (isCacheOrBytecode(rel)) return false
    return filterPortableSitePackage(rel, join(venvPackages, rel))
  })
}

function stagePython(pythonHome) {
  if (process.platform === 'win32') {
    stagePythonWindows(pythonHome)
    return
  }
  if (process.platform === 'linux') {
    stagePythonLinux(pythonHome)
    return
  }
  fail('The installer staging script currently supports the Windows NSIS and Linux AppImage packages only.')
}

function verifyPortableBundle() {
  const leaks = findPortabilityLeaks(stageRoot, repositoryRoot)
  if (!leaks.length) return
  const summary = leaks
    .slice(0, 20)
    .map(({ path, reason }) => `  - ${path} (${reason})`)
    .join('\n')
  fail(`Non-portable development paths were staged:\n${summary}`)
}

function nodeExecutable() {
  const configured = process.env.COLLIE_NODE_EXE
  if (configured) return resolve(configured)
  return process.execPath
}

function stageMcpRuntime() {
  if (process.platform === 'win32') {
    if (process.arch !== 'x64') {
      fail(`The alpha supports Windows x64 only; this build is ${process.arch}.`)
    }
    return stageMcpRuntimeWindows()
  }
  if (process.platform === 'linux') {
    if (process.arch !== 'x64') {
      fail(`The alpha supports x64 only; this build is ${process.arch}.`)
    }
    return stageMcpRuntimeLinux()
  }
  fail('The packaged MCP runtime currently supports Windows x64 and Linux x64 only.')
}

function stageMcpRuntimeWindows() {
  const sourceNode = nodeExecutable()
  if (!existsSync(sourceNode)) {
    fail(`Node runtime not found at ${sourceNode}. Set COLLIE_NODE_EXE correctly.`)
  }

  const runtimeRoot = join(stageParent, 'mcp-runtime')
  const rel = relative(uiRoot, runtimeRoot)
  if (!rel || rel.startsWith(`..${sep}`) || rel === '..') {
    fail(`Refusing to reset MCP staging path outside the UI repository: ${runtimeRoot}`)
  }
  rmSync(runtimeRoot, { recursive: true, force: true })
  mkdirSync(join(runtimeRoot, 'node'), { recursive: true })
  cpSync(sourceNode, join(runtimeRoot, 'node', 'node.exe'), { force: true })

  const nodeLicense = join(dirname(sourceNode), 'LICENSE')
  if (existsSync(nodeLicense)) {
    cpSync(nodeLicense, join(runtimeRoot, 'node', 'LICENSE'), { force: true })
  }

  copyTree(
    join(uiRoot, 'runtime', 'mcp-probe-server'),
    join(runtimeRoot, 'servers', 'mcp-probe-server')
  )
  return runtimeRoot
}

function stageMcpRuntimeLinux() {
  const sourceNode = nodeExecutable()
  if (!existsSync(sourceNode)) {
    fail(`Node runtime not found at ${sourceNode}. Set COLLIE_NODE_EXE correctly.`)
  }

  const runtimeRoot = join(stageParent, 'mcp-runtime')
  const rel = relative(uiRoot, runtimeRoot)
  if (!rel || rel.startsWith(`..${sep}`) || rel === '..') {
    fail(`Refusing to reset MCP staging path outside the UI repository: ${runtimeRoot}`)
  }
  rmSync(runtimeRoot, { recursive: true, force: true })
  // packaged_probe resolves the launcher as <mcp-runtime>/node/bin/node on Unix.
  mkdirSync(join(runtimeRoot, 'node', 'bin'), { recursive: true })
  cpSync(sourceNode, join(runtimeRoot, 'node', 'bin', 'node'), { force: true })

  const nodeLicense = join(dirname(sourceNode), 'LICENSE')
  if (existsSync(nodeLicense)) {
    cpSync(nodeLicense, join(runtimeRoot, 'node', 'LICENSE'), { force: true })
  }

  copyTree(
    join(uiRoot, 'runtime', 'mcp-probe-server'),
    join(runtimeRoot, 'servers', 'mcp-probe-server')
  )
  return runtimeRoot
}

function bundledPythonLauncher() {
  return process.platform === 'win32'
    ? join(stageRoot, 'python', 'python.exe')
    : join(stageRoot, 'python', 'bin', 'python3')
}

function verifyBundle() {
  const python = bundledPythonLauncher()
  const result = spawnSync(
    python,
    [
      '-c',
      [
        'import collie_core.runtime',
        'from collie_core.pet.v2 import asset_paths; asset_paths()',
        'import nanobot',
        'import websockets',
        'import pydantic',
        "print('Collie bundled core imports OK')"
      ].join(';')
    ],
    {
      cwd: stageRoot,
      encoding: 'utf8',
      windowsHide: true,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' }
    }
  )
  if (result.status !== 0) {
    fail(`Bundled Python verification failed:\n${result.stdout || ''}\n${result.stderr || ''}`)
  }
  process.stdout.write(result.stdout)
}

const pythonHome = pythonHomeFromVenv()
safeResetStage()
stageSource()
stagePython(pythonHome)
const mcpRuntimeRoot = stageMcpRuntime()
verifyBundle()
const mcpProbe = spawnSync(
  bundledPythonLauncher(),
  ['-m', 'collie_core.services.packaged_probe'],
  {
    cwd: stageRoot,
    encoding: 'utf8',
    windowsHide: true,
    env: {
      ...process.env,
      COLLIE_MCP_RUNTIME_ROOT: mcpRuntimeRoot,
      PYTHONDONTWRITEBYTECODE: '1'
    }
  }
)
if (mcpProbe.status !== 0) {
  fail(`Packaged MCP verification failed:\n${mcpProbe.stdout || ''}\n${mcpProbe.stderr || ''}`)
}
process.stdout.write(mcpProbe.stdout)
verifyPortableBundle()
console.log(`[stage-core] Ready: ${stageRoot}`)
