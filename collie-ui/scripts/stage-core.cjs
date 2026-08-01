const { cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync } = require('fs')
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
      /\.(py|png|json|md)$/i.test(rel)
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

function stagePython(pythonHome) {
  if (process.platform !== 'win32') {
    fail('The installer staging script currently supports the Windows NSIS package only.')
  }

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

  const venvSitePackages = join(coreRoot, '.venv', 'Lib', 'site-packages')
  copyTree(venvSitePackages, join(destination, 'Lib', 'site-packages'), (rel) => {
    if (!rel) return true
    if (isCacheOrBytecode(rel)) return false
    return filterPortableSitePackage(rel, join(venvSitePackages, rel))
  })
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
  if (process.platform !== 'win32') {
    fail('The packaged MCP runtime currently supports Windows x64 only.')
  }
  if (process.arch !== 'x64') {
    fail(`The alpha supports Windows x64 only; this build is ${process.arch}.`)
  }

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

function verifyBundle() {
  const python = join(stageRoot, 'python', 'python.exe')
  const result = spawnSync(
    python,
    [
      '-c',
      [
        'import collie_core.runtime',
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
  join(stageRoot, 'python', 'python.exe'),
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
