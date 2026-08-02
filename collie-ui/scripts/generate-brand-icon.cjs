const { app, BrowserWindow } = require('electron')
const { createHash } = require('node:crypto')
const { readFileSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join } = require('node:path')

const SOURCE_PATH = join(__dirname, '..', 'src', 'renderer', 'src', 'assets', 'portrait', 'happy.webp')
const PNG_PATH = join(__dirname, '..', 'build', 'icon.png')
const ICO_PATH = join(__dirname, '..', 'build', 'icon.ico')
const SOURCE_SHA256 = '147b183ca67aca26674e07f6639a7a9f4fa7c923feaca656b5a67aa228502537'
const BRAND_ORANGE = '#f29a3d'
const ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]

app.disableHardwareAcceleration()
app.setPath('userData', join(tmpdir(), 'collie-brand-icon-generator'))

function pngBuffer(dataUrl) {
  return Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64')
}

function createIco(images) {
  const header = Buffer.alloc(6 + images.length * 16)
  header.writeUInt16LE(0, 0)
  header.writeUInt16LE(1, 2)
  header.writeUInt16LE(images.length, 4)

  let offset = header.length
  images.forEach(({ size, png }, index) => {
    const entry = 6 + index * 16
    header.writeUInt8(size === 256 ? 0 : size, entry)
    header.writeUInt8(size === 256 ? 0 : size, entry + 1)
    header.writeUInt8(0, entry + 2)
    header.writeUInt8(0, entry + 3)
    header.writeUInt16LE(1, entry + 4)
    header.writeUInt16LE(32, entry + 6)
    header.writeUInt32LE(png.length, entry + 8)
    header.writeUInt32LE(offset, entry + 12)
    offset += png.length
  })

  return Buffer.concat([header, ...images.map(({ png }) => png)])
}

async function renderIcons(source) {
  const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true } })
  await window.loadURL('about:blank')

  const sourceUrl = `data:image/webp;base64,${source.toString('base64')}`
  const sizes = [...ICON_SIZES, 1024]
  const rendered = await window.webContents.executeJavaScript(`
    (async () => {
      const image = new Image()
      image.src = ${JSON.stringify(sourceUrl)}
      await image.decode()
      const canvas = document.createElement('canvas')
      const context = canvas.getContext('2d')

      return ${JSON.stringify(sizes)}.map((size) => {
        canvas.width = size
        canvas.height = size
        context.clearRect(0, 0, size, size)

        const radius = size * (11 / 35)
        context.beginPath()
        context.roundRect(0, 0, size, size, radius)
        context.clip()
        context.fillStyle = ${JSON.stringify(BRAND_ORANGE)}
        context.fillRect(0, 0, size, size)
        context.drawImage(image, 0, 0, size, size)

        return canvas.toDataURL('image/png')
      })
    })()
  `)

  window.destroy()
  return rendered.map((dataUrl, index) => ({ size: sizes[index], png: pngBuffer(dataUrl) }))
}

app.whenReady().then(async () => {
  const source = readFileSync(SOURCE_PATH)
  const sourceHash = createHash('sha256').update(source).digest('hex')
  if (sourceHash !== SOURCE_SHA256) {
    throw new Error(`Official portrait changed: expected ${SOURCE_SHA256}, received ${sourceHash}`)
  }

  const images = await renderIcons(source)
  writeFileSync(PNG_PATH, images.find(({ size }) => size === 1024).png)
  writeFileSync(ICO_PATH, createIco(images.filter(({ size }) => size !== 1024)))
  console.log(`Generated ${PNG_PATH} and ${ICO_PATH} from ${SOURCE_PATH}`)
  app.quit()
}).catch((error) => {
  console.error(error)
  app.exit(1)
})
