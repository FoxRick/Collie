// Exercise real Electron menus, images, and close events on each CI operating system.
const { spawnSync } = require('node:child_process')

if (!process.versions.electron) {
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  const result = spawnSync(require('electron'), [__filename], {
    env, stdio: 'inherit', timeout: 45000, windowsHide: true
  })
  if (result.error) throw result.error
  process.exit(result.status ?? 1)
}

const assert = require('node:assert/strict')
const { readFileSync, mkdtempSync } = require('node:fs')
const { join, dirname } = require('node:path')
const { tmpdir } = require('node:os')
const Module = require('node:module')
const ts = require('typescript')
const { app, BrowserWindow, Menu, Tray } = require('electron')

function loadSource(name) {
  const filename = join(__dirname, '..', 'src', 'main', `${name}.ts`)
  const loaded = new Module(filename, module)
  loaded.paths = Module._nodeModulePaths(dirname(filename))
  loaded._compile(ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText, filename)
  return loaded.exports
}

const { desktopMenu } = loadSource('desktop-menu')
const { createTrayImage, shouldHideOnClose, shouldQuitWithoutWindows } = loadSource('tray-platform')
app.disableHardwareAcceleration()
app.setPath('userData', mkdtempSync(join(tmpdir(), 'collie-tray-smoke-')))
app.on('window-all-closed', () => {})
let tray
const watchdog = setTimeout(() => { console.error('Native tray smoke timed out'); app.exit(1) }, 35000)

async function showAndCheck(menu, window, expected) {
  await new Promise((resolve, reject) => {
    menu.once('menu-will-show', () => setTimeout(() => {
      try {
        assert.deepEqual(menu.items.map(item => item.checked), expected)
        resolve()
      } catch (error) { reject(error) }
      finally { menu.closePopup(window) }
    }, 100))
    menu.popup({ window })
  })
}

app.whenReady().then(async () => {
  const icon = createTrayImage(join(__dirname, '..', 'build', 'icon.png'))
  assert.equal(icon.isEmpty(), false, 'tray image must load on this OS')
  if (process.platform === 'darwin') {
    assert.equal(icon.isTemplateImage(), true)
    assert.equal(icon.getSize().width, 16)
  }
  tray = new Tray(icon)
  const window = new BrowserWindow({ width: 320, height: 200, show: true,
    title: 'Collie native tray smoke', webPreferences: { sandbox: true } })
  await window.loadURL('about:blank')
  for (const activeId of [null, 'older-chat', 'a']) {
    let selected
    const menu = Menu.buildFromTemplate(desktopMenu({ activeId,
      conversations: [{ id: 'a', title: 'Work & travel' }, { id: 'b', title: 'Chat B' }]
    }, () => {}, action => { selected = action }, () => {}))
    tray.setContextMenu(menu)
    const recent = menu.items[2].submenu
    await showAndCheck(recent, window, [activeId === 'a', false])
    // MenuItem.click runs Electron's native checkbox toggle before our handler.
    recent.items[0].click()
    assert.equal(recent.items[0].checked, activeId === 'a')
    assert.deepEqual(selected, { type: 'open-chat', conversationId: 'a' })
  }
  const empty = Menu.buildFromTemplate(desktopMenu({ activeId: null, conversations: [] },
    () => {}, () => {}, () => {}))
  tray.setContextMenu(empty)
  assert.equal(empty.items[2].submenu.items[0].enabled, false)
  assert.equal(tray.isDestroyed(), false)
  window.on('close', event => {
    if (shouldHideOnClose(process.platform, true)) { event.preventDefault(); window.hide() }
  })
  window.close()
  assert.equal(window.isDestroyed(), !shouldHideOnClose(process.platform, true))
  if (!window.isDestroyed()) {
    assert.equal(window.isVisible(), false)
    window.show()
    assert.equal(window.isVisible(), true)
    window.destroy()
  }
  assert.equal(shouldQuitWithoutWindows(process.platform, true), process.platform === 'linux')
  tray.destroy()
  tray = null
  clearTimeout(watchdog)
  console.log(`Native desktop tray smoke passed on ${process.platform}`)
  app.quit()
}).catch(error => {
  console.error(error)
  tray?.destroy()
  app.exit(1)
})
