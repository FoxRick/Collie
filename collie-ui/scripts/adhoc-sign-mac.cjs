const { execSync } = require('child_process')

// macOS alpha builds have no Apple Developer certificate, but shipping the
// .app with signing fully disabled leaves Electron's stale embedded
// signature in place — the repackaged bundle's seal no longer matches, so
// Gatekeeper rejects it with "Collie is damaged and can't be opened" (an
// error with no right-click → Open bypass). Ad-hoc signing
// (`codesign --force --deep --sign -`) replaces every stale signature with
// a fresh valid one: macOS launches the app, and users only hit the normal
// "unidentified developer" prompt, bypassable via right-click → Open.
// Real Developer ID signing + notarization remains the $99/yr follow-up.
// afterPack is a TOP-LEVEL electron-builder hook (it fires for every
// platform), so this script must no-op on non-macOS runners.
exports.default = async function afterPack(context) {
  if (process.platform !== 'darwin') return
  const appPath = context.appOutDir
  console.log(`Ad-hoc codesigning ${appPath}`)
  execSync(`codesign --force --deep --sign - "${appPath}"`, {
    stdio: 'inherit',
  })
}
