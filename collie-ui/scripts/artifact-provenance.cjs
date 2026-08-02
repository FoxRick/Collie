const { createHash } = require('crypto')
const { existsSync, readdirSync, readFileSync, statSync, writeFileSync } = require('fs')
const { basename, join, resolve } = require('path')
const { packagedFileName, readPackagedProvenance } = require('./build-provenance.cjs')

const uiRoot = resolve(__dirname, '..')
const defaultReleaseRoot = resolve(uiRoot, 'dist')
const manifestName = 'collie-artifact-provenance.json'
const checksumsName = 'SHA256SUMS.txt'

function fail(message) {
  throw new Error(`[artifact-provenance] ${message}`)
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function installerName(version) {
  return `Collie-Setup-${version}.exe`
}

function payloadNames(version) {
  const installer = installerName(version)
  return [installer, `${installer}.blockmap`, 'alpha.yml', packagedFileName]
}

function allowedNames(version) {
  return new Set([...payloadNames(version), manifestName, checksumsName])
}

function assertReleaseDirectory(root, version, { complete = false } = {}) {
  if (!existsSync(root)) fail(`Release directory is missing: ${root}`)
  const entries = readdirSync(root, { withFileTypes: true })
  const allowed = allowedNames(version)
  const unexpected = entries
    .filter((entry) => !entry.isFile() || !allowed.has(entry.name))
    .map((entry) => entry.name)
    .sort()
  if (unexpected.length) fail(`Unexpected release output: ${unexpected.join(', ')}`)

  const required = complete ? allowed : new Set(payloadNames(version))
  const missing = [...required].filter((name) => !existsSync(join(root, name))).sort()
  if (missing.length) fail(`Missing release artifact(s): ${missing.join(', ')}`)
}

function releaseFiles(root, version) {
  assertReleaseDirectory(root, version)
  return payloadNames(version)
    .map((file) => {
      const path = join(root, file)
      return { file, bytes: statSync(path).size, sha256: sha256(path) }
    })
}

function manifestPath(root) {
  return join(root, manifestName)
}

function checksumsPath(root) {
  return join(root, checksumsName)
}

function writeChecksums(root, version) {
  const files = [...releaseFiles(root, version), {
    file: manifestName,
    bytes: statSync(manifestPath(root)).size,
    sha256: sha256(manifestPath(root))
  }]
  writeFileSync(
    checksumsPath(root),
    files.map(({ sha256: digest, file }) => `${digest}  ${file}`).join('\n') + '\n',
    'utf8'
  )
  return files
}

function writeManifest(root = process.env.COLLIE_RELEASE_DIR || defaultReleaseRoot) {
  const { provenance } = readPackagedProvenance(root)
  const artifacts = releaseFiles(root, provenance.productVersion)
  const manifest = { ...provenance, artifacts }
  writeFileSync(manifestPath(root), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  writeChecksums(root, provenance.productVersion)
  assertReleaseDirectory(root, provenance.productVersion, { complete: true })
  console.log(`[artifact-provenance] Wrote ${manifestPath(root)} and ${checksumsPath(root)}`)
  return manifest
}

function parseChecksums(root) {
  const entries = new Map()
  for (const [index, line] of readFileSync(checksumsPath(root), 'utf8').split(/\r?\n/).entries()) {
    if (!line) continue
    const match = line.match(/^([a-f0-9]{64})  ([^\\/]+)$/i)
    if (!match || entries.has(match[2])) fail(`Invalid ${checksumsName} entry at line ${index + 1}.`)
    entries.set(match[2], match[1].toLowerCase())
  }
  return entries
}

function verifyManifest(root = process.env.COLLIE_RELEASE_DIR || defaultReleaseRoot) {
  const { provenance } = readPackagedProvenance(root)
  assertReleaseDirectory(root, provenance.productVersion, { complete: true })
  const manifest = JSON.parse(readFileSync(manifestPath(root), 'utf8'))
  for (const field of ['schemaVersion', 'productVersion', 'gitSha', 'dirty', 'builtAt']) {
    if (manifest[field] !== provenance[field]) fail(`Manifest ${field} does not match packaged provenance.`)
  }
  const artifacts = releaseFiles(root, provenance.productVersion)
  if (JSON.stringify(manifest.artifacts) !== JSON.stringify(artifacts)) {
    fail('Artifact files or SHA-256 digests do not match the provenance manifest.')
  }
  const checksums = parseChecksums(root)
  const checksummedFiles = [...payloadNames(provenance.productVersion), manifestName]
  if (checksums.size !== checksummedFiles.length) fail(`${checksumsName} has unexpected entries.`)
  for (const file of checksummedFiles) {
    if (checksums.get(file) !== sha256(join(root, file))) {
      fail(`${checksumsName} does not match ${file}.`)
    }
  }
  console.log(`[artifact-provenance] Verified ${manifestPath(root)} and ${checksumsPath(root)}`)
  return manifest
}

function main() {
  const root = process.argv[3] ? resolve(process.argv[3]) : undefined
  if (process.argv[2] === 'write') return writeManifest(root)
  if (process.argv[2] === 'verify') return verifyManifest(root)
  fail('Usage: node scripts/artifact-provenance.cjs <write|verify> [release-directory]')
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
  allowedNames,
  assertReleaseDirectory,
  checksumsName,
  manifestName,
  payloadNames,
  releaseFiles,
  verifyManifest,
  writeManifest
}
