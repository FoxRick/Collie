const { app, BrowserWindow } = require('electron')
const { mkdirSync } = require('fs')
const { join } = require('path')
const { pathToFileURL } = require('url')

async function capture(window, name) {
  await new Promise((resolve) => setTimeout(resolve, 700))
  const image = await window.webContents.capturePage()
  const outputDir = join(__dirname, '..', 'screenshots', 'ui-refresh')
  mkdirSync(outputDir, { recursive: true })
  require('fs').writeFileSync(join(outputDir, `${name}.png`), image.toPNG())
}

async function select(window, label) {
  await window.webContents.executeJavaScript(`
    (() => {
      const button = Array.from(document.querySelectorAll('button')).find(
        (item) => item.textContent.trim().startsWith(${JSON.stringify(label)})
      )
      if (!button) throw new Error('Navigation item not found: ${label}')
      button.click()
    })()
  `)
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const selected = await window.webContents.executeJavaScript(`
      Array.from(document.querySelectorAll('button')).some(
        (item) => item.textContent.trim().startsWith(${JSON.stringify(label)}) &&
          item.getAttribute('aria-current') === 'page'
      )
    `)
    if (selected) return
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(`Navigation did not settle: ${label}`)
}

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1120,
    height: 780,
    show: true,
    opacity: 0.01,
    skipTaskbar: true,
    backgroundColor: '#f4f3ef',
    webPreferences: { backgroundThrottling: false }
  })
  const page = pathToFileURL(join(__dirname, '..', 'out', 'renderer', 'index.html')).toString()
  await window.loadURL(`${page}?preview`)
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const ready = await window.webContents.executeJavaScript(
      `Array.from(document.querySelectorAll('button')).some((item) => item.textContent.trim() === 'Chat')`
    )
    if (ready) break
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  await capture(window, 'chat')
  for (const label of ['Agents', 'Skills', 'Routines', 'Settings']) {
    await select(window, label)
    await capture(window, label.toLowerCase())
  }
  await select(window, 'Audio & input')
  await capture(window, 'settings-audio-input')
  window.destroy()
  app.quit()
})
