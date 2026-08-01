const { readFileSync, readdirSync, statSync } = require('fs')
const { basename, join, relative, resolve } = require('path')
const { pathToFileURL } = require('url')

function normalizedPath(path) {
  return path.replaceAll('\\', '/')
}

function nonPortableSitePackageReason(relativePath, contents = '') {
  const normalized = normalizedPath(relativePath).toLowerCase()
  const name = basename(normalized)

  if (name === 'direct_url.json') return 'local-install metadata'
  if (name.endsWith('.egg-link')) return 'editable-install link'
  if (/^(?:__|_)?editable(?:[_.-]|$)/.test(name)) return 'editable-install artifact'

  if (name.endsWith('.pth')) {
    const hasAbsolutePath = String(contents)
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#') && !line.startsWith('import '))
      .some((line) => (
        /^[a-z]:[\\/]/i.test(line) ||
        /^\\\\/.test(line) ||
        /^file:\/\//i.test(line) ||
        /^\//.test(line)
      ))
    if (hasAbsolutePath) return 'absolute path in .pth file'
  }

  return null
}

function isNonPortableSitePackageArtifact(relativePath, contents = '') {
  return nonPortableSitePackageReason(relativePath, contents) !== null
}

function repositoryPathVariants(repositoryRoot) {
  const absolute = resolve(repositoryRoot)
  const slashed = normalizedPath(absolute)
  return Array.from(new Set([
    absolute,
    slashed,
    JSON.stringify(absolute).slice(1, -1),
    pathToFileURL(absolute).href
  ])).map((value) => value.toLowerCase())
}

function containsRepositoryPath(contents, repositoryRoot) {
  const text = Buffer.isBuffer(contents) ? contents.toString('utf8') : String(contents)
  const lower = text.toLowerCase()
  return repositoryPathVariants(repositoryRoot).some((variant) => lower.includes(variant))
}

function walkFiles(root) {
  const files = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) files.push(...walkFiles(path))
    else if (entry.isFile()) files.push(path)
  }
  return files
}

function findPortabilityLeaks(root, repositoryRoot) {
  const leaks = []
  for (const path of walkFiles(root)) {
    const rel = normalizedPath(relative(root, path))
    const contents = readFileSync(path)
    const artifactReason = nonPortableSitePackageReason(rel, contents.toString('utf8'))
    if (artifactReason) {
      leaks.push({ path: rel, reason: artifactReason })
      continue
    }
    if (containsRepositoryPath(contents, repositoryRoot)) {
      leaks.push({ path: rel, reason: 'absolute repository path' })
    }
  }
  return leaks
}

function filterPortableSitePackage(relativePath, sourcePath) {
  if (!relativePath || statSync(sourcePath).isDirectory()) return true
  const name = basename(normalizedPath(relativePath)).toLowerCase()
  const needsContents = name.endsWith('.pth')
  const contents = needsContents ? readFileSync(sourcePath, 'utf8') : ''
  return !isNonPortableSitePackageArtifact(relativePath, contents)
}

module.exports = {
  containsRepositoryPath,
  filterPortableSitePackage,
  findPortabilityLeaks,
  isNonPortableSitePackageArtifact,
  nonPortableSitePackageReason,
  repositoryPathVariants
}
