const { existsSync, mkdirSync, readFileSync, writeFileSync } = require('fs')
const { spawnSync } = require('child_process')
const { join, resolve } = require('path')

const uiRoot = resolve(__dirname, '..')
const repositoryRoot = resolve(uiRoot, '..')
const packagePath = join(uiRoot, 'package.json')
const stagingPath = join(uiRoot, '.electron-bundle', 'build-provenance.json')
const packagedFileName = 'collie-build-provenance.json'

function fail(message) {
  throw new Error(`[build-provenance] ${message}`)
}

function runGit(args) {
  const result = spawnSync('git', args, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    windowsHide: true
  })
  if (result.error) fail(`Could not run git ${args.join(' ')}: ${result.error.message}`)
  if (result.status !== 0) {
    fail(`git ${args.join(' ')} failed: ${(result.stderr || result.stdout || '').trim()}`)
  }
  return (result.stdout || '').trim()
}

function assertCleanRepository(git = runGit) {
  const status = git(['status', '--porcelain=v1', '--untracked-files=all'])
  if (status) {
    fail(
      'Refusing to package a dirty Git worktree. Commit, stash, or remove local changes before packaging.'
    )
  }
}

function packageVersion() {
  const value = JSON.parse(readFileSync(packagePath, 'utf8')).version
  if (typeof value !== 'string' || !value) fail(`Missing package version in ${packagePath}.`)
  return value
}

function makeProvenance(git = runGit, builtAt = new Date().toISOString()) {
  assertCleanRepository(git)
  const gitSha = git(['rev-parse', 'HEAD'])
  if (!/^[0-9a-f]{40,64}$/i.test(gitSha)) fail(`Git did not return a full commit SHA: ${gitSha}`)
  return {
    schemaVersion: 1,
    productVersion: packageVersion(),
    gitSha: gitSha.toLowerCase(),
    dirty: false,
    builtAt
  }
}

function validateProvenance(value, expectedVersion = packageVersion()) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('Provenance must be a JSON object.')
  if (value.schemaVersion !== 1) fail(`Unsupported provenance schema: ${value.schemaVersion}`)
  if (value.productVersion !== expectedVersion) {
    fail(`Provenance productVersion ${JSON.stringify(value.productVersion)} does not match ${expectedVersion}.`)
  }
  if (!/^[0-9a-f]{40,64}$/i.test(value.gitSha || '')) fail('Provenance gitSha is not a full commit SHA.')
  if (value.dirty !== false) fail('Release provenance must record dirty: false.')
  if (typeof value.builtAt !== 'string' || Number.isNaN(Date.parse(value.builtAt))) {
    fail('Provenance builtAt must be an ISO-8601 timestamp.')
  }
  return value
}

function writeProvenance() {
  const provenance = makeProvenance()
  mkdirSync(resolve(stagingPath, '..'), { recursive: true })
  writeFileSync(stagingPath, `${JSON.stringify(provenance, null, 2)}\n`, 'utf8')
  return provenance
}

function ensureProvenance() {
  const expected = makeProvenance()
  if (existsSync(stagingPath)) {
    const current = validateProvenance(JSON.parse(readFileSync(stagingPath, 'utf8')))
    if (current.gitSha === expected.gitSha && current.productVersion === expected.productVersion) {
      return current
    }
  }
  mkdirSync(resolve(stagingPath, '..'), { recursive: true })
  writeFileSync(stagingPath, `${JSON.stringify(expected, null, 2)}\n`, 'utf8')
  return expected
}

function packagedProvenancePath(resources = process.env.COLLIE_PACKAGED_RESOURCES) {
  const root = resources
    ? resolve(resources)
    : resolve(uiRoot, 'dist', 'win-unpacked', 'resources')
  return join(root, packagedFileName)
}

function readPackagedProvenance(resources) {
  const path = packagedProvenancePath(resources)
  if (!existsSync(path)) fail(`Packaged provenance is missing: ${path}`)
  const provenance = validateProvenance(JSON.parse(readFileSync(path, 'utf8')))
  return { path, provenance }
}

function main() {
  const command = process.argv[2]
  if (command === 'write') {
    console.log(JSON.stringify(writeProvenance(), null, 2))
    return
  }
  if (command === 'ensure') {
    console.log(JSON.stringify(ensureProvenance(), null, 2))
    return
  }
  if (command === 'print') {
    console.log(JSON.stringify(readPackagedProvenance(process.argv[3]).provenance, null, 2))
    return
  }
  if (command === 'verify') {
    const { path, provenance } = readPackagedProvenance(process.argv[3])
    console.log(`[build-provenance] Verified ${path}`)
    console.log(JSON.stringify(provenance, null, 2))
    return
  }
  fail('Usage: node scripts/build-provenance.cjs <write|ensure|print|verify> [resources-directory]')
}

if (require.main === module) {
  try {
    main()
  } catch (error) {
    console.error(error.message || error)
    process.exit(1)
  }
}

module.exports = {
  assertCleanRepository,
  makeProvenance,
  packagedFileName,
  packagedProvenancePath,
  readPackagedProvenance,
  stagingPath,
  validateProvenance,
  writeProvenance,
  ensureProvenance
}
