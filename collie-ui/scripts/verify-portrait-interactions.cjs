const { app, BrowserWindow } = require('electron')
const { join } = require('path')
const { pathToFileURL } = require('url')

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1120,
    height: 780,
    show: true,
    webPreferences: { backgroundThrottling: false }
  })
  const page = pathToFileURL(join(__dirname, '..', 'out', 'renderer', 'index.html')).toString()
  await window.loadURL(`${page}?preview=1`)
  await wait(400)

  const initial = await window.webContents.executeJavaScript(`(() => {
    const stage = document.querySelector('.collie-portrait-stage')
    return {
      className: stage.className,
      gazeDirection: document.querySelector('.collie-portrait-ring').dataset.gazeDirection,
      pawOpacity: getComputedStyle(document.querySelector('.collie-portrait-paw')).opacity
    }
  })()`)

  const gaze = await window.webContents.executeJavaScript(`(async () => {
    const ring = document.querySelector('.collie-portrait-ring')
    const rect = ring.getBoundingClientRect()
    ring.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true,
      clientX: rect.right - 3,
      clientY: rect.top + rect.height / 2
    }))
    await new Promise((resolve) => setTimeout(resolve, 100))
    return {
      gazeDirection: ring.dataset.gazeDirection
    }
  })()`)

  const click = await window.webContents.executeJavaScript(`(async () => {
    const stage = document.querySelector('.collie-portrait-stage')
    document.querySelector('.collie-portrait-ring').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 240))
    return {
      className: stage.className,
      pawClassName: document.querySelector('.collie-portrait-paw').className,
      pawOpacity: getComputedStyle(document.querySelector('.collie-portrait-paw')).opacity
    }
  })()`)

  const typing = await window.webContents.executeJavaScript(`(async () => {
    const stage = document.querySelector('.collie-portrait-stage')
    document.querySelector('.collie-portrait-ring').dispatchEvent(new PointerEvent('pointerleave', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 900))
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

  console.log(JSON.stringify({ initial, gaze, click, typing }, null, 2))
  window.destroy()
  app.quit()
})
