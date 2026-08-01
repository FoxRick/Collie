const { app, BrowserWindow } = require('electron')
const { join } = require('path')
const { pathToFileURL } = require('url')

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1120,
    height: 780,
    show: false,
    webPreferences: { backgroundThrottling: false }
  })
  const page = pathToFileURL(join(__dirname, '..', 'out', 'renderer', 'index.html')).toString()
  await window.loadURL(`${page}?preview=1`)
  await wait(400)

  const initial = await window.webContents.executeJavaScript(`(() => {
    const stage = document.querySelector('.collie-portrait-stage')
    return {
      className: stage.className,
      gazeX: getComputedStyle(stage).getPropertyValue('--gaze-x'),
      gazeY: getComputedStyle(stage).getPropertyValue('--gaze-y'),
      pawOpacity: getComputedStyle(document.querySelector('.collie-portrait-paw')).opacity
    }
  })()`)

  const gaze = await window.webContents.executeJavaScript(`(async () => {
    const workspace = document.querySelector('.workspace')
    const stage = document.querySelector('.collie-portrait-stage')
    workspace.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true,
      clientX: innerWidth - 20,
      clientY: 40
    }))
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    return {
      gazeX: stage.style.getPropertyValue('--gaze-x'),
      gazeY: stage.style.getPropertyValue('--gaze-y')
    }
  })()`)

  const hover = await window.webContents.executeJavaScript(`(async () => {
    const stage = document.querySelector('.collie-portrait-stage')
    stage.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 240))
    return {
      className: stage.className,
      pawClassName: document.querySelector('.collie-portrait-paw').className,
      pawOpacity: getComputedStyle(document.querySelector('.collie-portrait-paw')).opacity
    }
  })()`)

  const typing = await window.webContents.executeJavaScript(`(async () => {
    const stage = document.querySelector('.collie-portrait-stage')
    stage.dispatchEvent(new PointerEvent('pointerout', { bubbles: true }))
    const textarea = document.querySelector('textarea')
    textarea.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    setter.call(textarea, 'Hello Collie')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 300))
    return {
      className: stage.className,
      focused: document.activeElement === textarea,
      value: textarea.value
    }
  })()`)

  console.log(JSON.stringify({ initial, gaze, hover, typing }, null, 2))
  window.destroy()
  app.quit()
})
