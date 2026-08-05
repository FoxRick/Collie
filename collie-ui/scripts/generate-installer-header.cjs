const { app, BrowserWindow } = require('electron')
const { createHash } = require('node:crypto')
const { readFileSync, writeFileSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join } = require('node:path')

// Derives the small NSIS wizard header image (150x57, 24-bit BMP) from the
// registered app icon. Re-run after the icon changes: npm run installer-header:generate
const SOURCE_PATH = join(__dirname, '..', 'build', 'icon.png')
const OUTPUT_PATH = join(__dirname, '..', 'build', 'installer-header.bmp')
const SOURCE_SHA256 = 'e84369ae2a2e8b33c1d04eead500030d51f632bbf1797b8a576200decbf46c19'

const WIDTH = 150
const HEIGHT = 57
const ICON_SIZE = 44
const ICON_X = 6
const ICON_Y = 6
const TEXT_X = 58

app.disableHardwareAcceleration()
app.setPath('userData', join(tmpdir(), 'collie-installer-header-generator'))

// 24-bit bottom-up BMP with 4-byte row padding (the format NSIS/MUI2 expects).
function bmpFromRgba(rgba, width, height) {
  const rowSize = Math.ceil((width * 3) / 4) * 4
  const pixelBytes = rowSize * height
  const header = Buffer.alloc(54)
  header.write('BM', 0, 2, 'ascii')
  header.writeUInt32LE(54 + pixelBytes, 2)
  header.writeUInt32LE(54, 10)
  header.writeUInt32LE(40, 14)
  header.writeInt32LE(width, 18)
  header.writeInt32LE(height, 22)
  header.writeUInt16LE(1, 26)
  header.writeUInt16LE(24, 28)
  header.writeUInt32LE(0, 30)
  header.writeUInt32LE(pixelBytes, 34)
  header.writeInt32LE(2835, 38)
  header.writeInt32LE(2835, 42)

  const pixels = Buffer.alloc(pixelBytes)
  for (let y = 0; y < height; y++) {
    const srcRow = (height - 1 - y) * width * 4
    const dstRow = y * rowSize
    for (let x = 0; x < width; x++) {
      const src = srcRow + x * 4
      const dst = dstRow + x * 3
      pixels[dst] = rgba[src + 2] // B
      pixels[dst + 1] = rgba[src + 1] // G
      pixels[dst + 2] = rgba[src] // R
    }
  }
  return Buffer.concat([header, pixels])
}

async function renderHeader(source) {
  const window = new BrowserWindow({ show: false, webPreferences: { sandbox: true } })
  await window.loadURL('about:blank')

  const sourceUrl = `data:image/png;base64,${source.toString('base64')}`
  const result = await window.webContents.executeJavaScript(`
    (async () => {
      const image = new Image()
      image.src = ${JSON.stringify(sourceUrl)}
      await image.decode()

      const canvas = document.createElement('canvas')
      canvas.width = ${WIDTH}
      canvas.height = ${HEIGHT}
      const context = canvas.getContext('2d')

      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, ${WIDTH}, ${HEIGHT})

      // Brand portrait (already rounded + orange from the app icon) top-left.
      context.drawImage(image, ${ICON_X}, ${ICON_Y}, ${ICON_SIZE}, ${ICON_SIZE})

      context.textAlign = 'left'
      context.textBaseline = 'alphabetic'
      context.fillStyle = '#1f2937'
      context.font = 'bold 22px "DejaVu Sans", "Segoe UI", Arial, sans-serif'
      context.fillText('Collie', ${TEXT_X}, 34)

      context.fillStyle = '#6b7280'
      context.font = '11px "DejaVu Sans", "Segoe UI", Arial, sans-serif'
      context.fillText('Your personal AI', ${TEXT_X}, 50)

      return Array.from(context.getImageData(0, 0, ${WIDTH}, ${HEIGHT}).data)
    })()
  `)

  window.destroy()
  return Buffer.from(result)
}

app.whenReady().then(async () => {
  const source = readFileSync(SOURCE_PATH)
  const sourceHash = createHash('sha256').update(source).digest('hex')
  if (sourceHash !== SOURCE_SHA256) {
    throw new Error(`App icon changed: expected ${SOURCE_SHA256}, received ${sourceHash}`)
  }

  const rgba = await renderHeader(source)
  const bmp = bmpFromRgba(rgba, WIDTH, HEIGHT)
  writeFileSync(OUTPUT_PATH, bmp)
  console.log(`Generated ${OUTPUT_PATH} (${WIDTH}x${HEIGHT}, ${bmp.length} bytes)`)
  app.quit()
}).catch((error) => {
  console.error(error)
  app.exit(1)
})
