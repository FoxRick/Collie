const { app, BrowserWindow } = require('electron')
const { mkdirSync, writeFileSync } = require('fs')
const { join } = require('path')
const { pathToFileURL } = require('url')

const sizes = [
  ['wide', 1180, 780],
  ['normal', 940, 720],
  ['compact', 780, 680],
  ['narrow', 650, 640]
]

async function settle(window) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const ready = await window.webContents.executeJavaScript(
      `Boolean(document.querySelector('.collie-portrait-stage') && document.querySelector('textarea'))`
    ).catch(() => false)
    if (ready) return
    await new Promise((resolve) => setTimeout(resolve, 80))
  }
  throw new Error('Portrait or composer did not render.')
}

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1180,
    height: 780,
    show: true,
    opacity: 0.01,
    skipTaskbar: true,
    backgroundColor: '#f4f3ef',
    webPreferences: { backgroundThrottling: false }
  })
  const outputDir = join(__dirname, '..', 'screenshots', 'portrait-qa')
  mkdirSync(outputDir, { recursive: true })
  const page = pathToFileURL(join(__dirname, '..', 'out', 'renderer', 'index.html')).toString()
  await window.loadURL(`${page}?preview=1`)
  await settle(window)

  const report = []
  for (const [name, width, height] of sizes) {
    window.setSize(width, height)
    await new Promise((resolve) => setTimeout(resolve, 250))
    for (const theme of ['light', 'dark']) {
      await window.webContents.executeJavaScript(
        `document.documentElement.classList.toggle('dark', ${theme === 'dark'})`
      )
      await new Promise((resolve) => setTimeout(resolve, 120))
      const image = await window.webContents.capturePage()
      writeFileSync(join(outputDir, `${name}-${theme}.png`), image.toPNG())
      const metrics = await window.webContents.executeJavaScript(`(() => {
        const stage = document.querySelector('.collie-portrait-stage')
        const ring = document.querySelector('.collie-portrait-ring')
        const status = document.querySelector('.collie-portrait-status')
        const composer = document.querySelector('.composer')
        const paw = document.querySelector('.collie-portrait-paw')
        const stageRect = stage.getBoundingClientRect()
        const statusRect = status.getBoundingClientRect()
        const composerRect = composer.getBoundingClientRect()
        return {
          viewport: [innerWidth, innerHeight],
          portraitWidth: Math.round(stageRect.width),
          composerWidth: Math.round(composerRect.width),
          statusBelowPortrait: statusRect.top >= stageRect.top + ring.getBoundingClientRect().height,
          pawDisplay: getComputedStyle(paw).display,
          hasOverflowCollision: stageRect.right > composerRect.left
        }
      })()`)
      report.push({ name, theme, ...metrics })
    }
  }

  window.setSize(1180, 780)
  await window.webContents.executeJavaScript(`(() => {
    document.documentElement.classList.remove('dark')
    document.querySelector('.collie-portrait-ring').dispatchEvent(
      new MouseEvent('click', { bubbles: true })
    )
  })()`)
  await new Promise((resolve) => setTimeout(resolve, 320))
  writeFileSync(
    join(outputDir, 'wide-paw-over-ring.png'),
    (await window.webContents.capturePage()).toPNG()
  )
  const paw = await window.webContents.executeJavaScript(`(() => {
    const ring = document.querySelector('.collie-portrait-ring').getBoundingClientRect()
    const pawElement = document.querySelector('.collie-portrait-paw')
    const paw = pawElement.getBoundingClientRect()
    return {
      opacity: getComputedStyle(pawElement).opacity,
      crossesRightEdge: paw.right > ring.right,
      crossesBottomEdge: paw.bottom > ring.bottom,
      overlapsRing: paw.left < ring.right && paw.top < ring.bottom
    }
  })()`)
  const attentive = await window.webContents.executeJavaScript(`(async () => {
    const stage = document.querySelector('.collie-portrait-stage')
    stage.dispatchEvent(new PointerEvent('pointerout', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 900))
    const textarea = document.querySelector('textarea')
    textarea.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    setter.call(textarea, 'Hello Collie')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 300))
    return {
      className: stage.className,
      status: document.querySelector('.collie-portrait-status').textContent.trim()
    }
  })()`)
  writeFileSync(
    join(outputDir, 'wide-attentive-typing.png'),
    (await window.webContents.capturePage()).toPNG()
  )
  const errors = await window.webContents.executeJavaScript(
    `document.querySelector('.vite-error-overlay')?.textContent || ''`
  )
  console.log(JSON.stringify({ report, paw, attentive, errors }, null, 2))
  window.destroy()
  app.quit()
})
