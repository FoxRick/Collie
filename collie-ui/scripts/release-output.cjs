const { existsSync, mkdirSync, readdirSync, renameSync, rmSync, statSync } = require('fs')
const { basename, dirname, join, relative, resolve } = require('path')
const { packagedFileName } = require('./build-provenance.cjs')
const { payloadNames } = require('./artifact-provenance.cjs')

const uiRoot = resolve(__dirname, '..')
const distRoot = resolve(uiRoot, 'dist')

function fail(message) {
  throw new Error(`[release-output] ${message}`)
}

function releaseSlug(provenance) {
  if (!/^[0-9A-Za-z][0-9A-Za-z.+-]*$/.test(provenance.productVersion || '')) {
    fail(`Unsafe product version for release output: ${provenance.productVersion}`)
  }
  if (!/^[a-f0-9]{40,64}$/i.test(provenance.gitSha || '')) {
    fail(`Invalid Git SHA for release output: ${provenance.gitSha}`)
  }
  return `${provenance.productVersion}-${provenance.gitSha.slice(0, 12).toLowerCase()}`
}

function releasePaths(provenance, root = distRoot) {
  const slug = releaseSlug(provenance)
  return {
    root: resolve(root),
    staging: resolve(root, `.staging-${slug}`),
    release: resolve(root, `release-${slug}`)
  }
}

function assertManagedDirectory(path, root, label) {
  const resolvedRoot = resolve(root)
  const resolvedPath = resolve(path)
  const rel = relative(resolvedRoot, resolvedPath)
  if (!rel || rel.startsWith('..') || dirname(resolvedPath) !== resolvedRoot) {
    fail(`Refusing to clear ${label} outside the managed release output: ${resolvedPath}`)
  }
  if (!basename(resolvedPath).match(/^(?:\.staging-|release-)/)) {
    fail(`Refusing to clear unexpected ${label}: ${resolvedPath}`)
  }
}

function resetManagedDirectory(path, root = distRoot, label = 'release output') {
  assertManagedDirectory(path, root, label)
  rmSync(path, { recursive: true, force: true })
  mkdirSync(path, { recursive: true })
}

function prepareReleaseOutput(provenance, root = distRoot) {
  const paths = releasePaths(provenance, root)
  mkdirSync(paths.root, { recursive: true })
  resetManagedDirectory(paths.staging, paths.root, 'staging output')
  resetManagedDirectory(paths.release, paths.root, 'release output')
  return paths
}

function assertBuilderOutput(staging, version) {
  const installer = `Collie-Setup-${version}.exe`
  const expectedFiles = new Set([installer, `${installer}.blockmap`, 'alpha.yml'])
  const stagingOnlyFiles = new Set(['builder-debug.yml'])
  const entries = readdirSync(staging, { withFileTypes: true })
  const unexpected = entries
    .filter((entry) => (
      (entry.isDirectory() && entry.name !== 'win-unpacked') ||
      (entry.isFile() && !expectedFiles.has(entry.name) && !stagingOnlyFiles.has(entry.name)) ||
      (!entry.isDirectory() && !entry.isFile())
    ))
    .map((entry) => entry.name)
    .sort()
  if (unexpected.length) fail(`Unexpected electron-builder output: ${unexpected.join(', ')}`)
  for (const name of expectedFiles) {
    const path = join(staging, name)
    if (!existsSync(path) || !statSync(path).isFile()) fail(`Missing electron-builder artifact: ${name}`)
  }
  const packagedProvenance = join(staging, 'win-unpacked', 'resources', packagedFileName)
  if (!existsSync(packagedProvenance) || !statSync(packagedProvenance).isFile()) {
    fail(`Missing packaged provenance: ${packagedProvenance}`)
  }
}

function moveReleaseArtifacts(paths, provenance) {
  assertBuilderOutput(paths.staging, provenance.productVersion)
  for (const file of payloadNames(provenance.productVersion).slice(0, 3)) {
    renameSync(join(paths.staging, file), join(paths.release, file))
  }
  renameSync(
    join(paths.staging, 'win-unpacked', 'resources', packagedFileName),
    join(paths.release, packagedFileName)
  )
  return paths.release
}

function removeStagingOutput(paths) {
  assertManagedDirectory(paths.staging, paths.root, 'staging output')
  rmSync(paths.staging, { recursive: true, force: true })
}

module.exports = {
  assertBuilderOutput,
  prepareReleaseOutput,
  releasePaths,
  releaseSlug,
  removeStagingOutput,
  resetManagedDirectory,
  moveReleaseArtifacts
}
