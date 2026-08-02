const { ensureProvenance } = require('./build-provenance.cjs')

exports.default = async function beforePack() {
  // This hook protects direct electron-builder invocations as well as npm run dist.
  ensureProvenance()
}
