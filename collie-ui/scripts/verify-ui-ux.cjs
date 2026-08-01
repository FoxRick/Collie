const { mkdirSync, writeFileSync } = require('fs')
const { dirname, resolve } = require('path')

const debugPort = Number(process.env.COLLIE_DEBUG_PORT || 9223)
const outputDir = resolve(process.env.COLLIE_UI_UX_OUTPUT || '../.local-runtime-logs')

function delay(ms) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms))
}

async function target() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${debugPort}/json`)
      const targets = await response.json()
      const page = targets.find((item) => item.type === 'page' && item.title === 'Collie')
      if (page) return page
    } catch {
      // Electron is still starting.
    }
    await delay(250)
  }
  throw new Error(`No Collie DevTools target appeared on port ${debugPort}.`)
}

async function connect(url) {
  return await new Promise((resolveSocket, reject) => {
    const socket = new WebSocket(url)
    socket.addEventListener('open', () => resolveSocket(socket), { once: true })
    socket.addEventListener('error', () => reject(new Error('DevTools WebSocket failed.')), {
      once: true
    })
  })
}

async function main() {
  const page = await target()
  const socket = await connect(page.webSocketDebuggerUrl)
  const pending = new Map()
  const rendererErrors = []
  let sequence = 0

  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data))
    if (message.method === 'Runtime.exceptionThrown') {
      rendererErrors.push(
        message.params.exceptionDetails.exception?.description ||
          message.params.exceptionDetails.text ||
          'Unknown renderer exception'
      )
    }
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
      rendererErrors.push(message.params.entry.text)
    }
    if (!message.id || !pending.has(message.id)) return
    const { resolveCommand, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(message.error.message))
    else resolveCommand(message.result)
  })

  function command(method, params = {}) {
    const id = ++sequence
    return new Promise((resolveCommand, reject) => {
      pending.set(id, { resolveCommand, reject })
      socket.send(JSON.stringify({ id, method, params }))
    })
  }

  async function evaluate(expression) {
    const result = await command('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true
    })
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || 'Renderer evaluation failed.')
    }
    return result.result.value
  }

  async function clickButton(label, startsWith = false) {
    await evaluate(`(async () => {
      const button = Array.from(document.querySelectorAll('button')).find((item) => {
        const text = item.textContent.trim()
        return ${startsWith ? 'text.startsWith' : 'text ==='}(${JSON.stringify(label)})
      })
      if (!button) throw new Error(${JSON.stringify(`Missing button: ${label}`)})
      button.click()
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 350))
    })()`)
  }

  async function waitUntil(expression, attempts = 40) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await evaluate(expression).catch(() => false)) return true
      await delay(250)
    }
    return false
  }

  async function setWindowSize(width, height) {
    await command('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false
    })
    await delay(250)
  }

  async function capture(name) {
    const result = await command('Page.captureScreenshot', { format: 'png', fromSurface: true })
    const path = resolve(outputDir, name)
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, Buffer.from(result.data, 'base64'))
    return path
  }

  async function auditScreen(name) {
    return await evaluate(`(() => {
      const visible = (element) => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        return style.visibility !== 'hidden' && style.display !== 'none' &&
          rect.width > 0 && rect.height > 0
      }
      const controls = Array.from(document.querySelectorAll(
        'button, a[href], input, textarea, select'
      )).filter(visible)
      const nameFor = (element) =>
        element.getAttribute('aria-label') ||
        element.getAttribute('title') ||
        element.textContent?.trim() ||
        element.getAttribute('placeholder') ||
        (element.id
          ? document.querySelector('label[for="' + CSS.escape(element.id) + '"]')?.textContent?.trim()
          : '') ||
        ''
      return {
        name: ${JSON.stringify(name)},
        heading: document.querySelector('main h1')?.textContent?.trim() || '',
        viewport: { width: innerWidth, height: innerHeight },
        horizontalOverflow: document.documentElement.scrollWidth >
          document.documentElement.clientWidth + 1,
        unlabeledControls: controls
          .filter((element) => !nameFor(element))
          .map((element) => element.outerHTML.slice(0, 180)),
        overlay: Boolean(document.querySelector(
          '[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay'
        )),
        dialogCount: document.querySelectorAll('[role="dialog"]').length
      }
    })()`)
  }

  await command('Runtime.enable')
  await command('Log.enable')
  await command('Page.enable')

  if (new URL(page.url).searchParams.has('preview')) {
    await command('Page.reload', { ignoreCache: true })
    await waitUntil(`Boolean(document.querySelector('body'))`)
  }

  const { core, ipcProbe } = await evaluate(`(async () => {
    const { token, ...safeCore } = await window.collie.coreState()
    const ipcProbe = await new Promise((resolveProbe) => {
      const socket = new WebSocket(
        'ws://127.0.0.1:' + safeCore.port,
        'collie-' + token
      )
      const timer = setTimeout(() => {
        socket.close()
        resolveProbe({ ok: false, error: 'timeout' })
      }, 3000)
      socket.addEventListener('open', () => {
        socket.send(JSON.stringify({ type: 'get_status', id: 'ui-ux-probe' }))
      })
      socket.addEventListener('message', (event) => {
        const data = JSON.parse(String(event.data))
        if (data.id !== 'ui-ux-probe') return
        clearTimeout(timer)
        socket.close()
        resolveProbe({ ok: data.type === 'ok', type: data.type, message: data.message || '' })
      })
      socket.addEventListener('error', () => {
        clearTimeout(timer)
        socket.close()
        resolveProbe({ ok: false, error: 'websocket error' })
      })
    })
    return { core: safeCore, ipcProbe }
  })()`)
  const hasApp = await evaluate(
    `Boolean(document.querySelector('nav[aria-label="Primary navigation"]'))`
  )
  if (!hasApp) {
    const previewUrl = new URL(page.url)
    previewUrl.searchParams.set('preview', '1')
    await command('Page.navigate', { url: previewUrl.toString() })
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const ready = await evaluate(
        `Boolean(document.querySelector('nav[aria-label="Primary navigation"]'))`
      ).catch(() => false)
      if (ready) break
      await delay(250)
    }
  }

  await setWindowSize(1120, 780)
  await clickButton('General Chat', true)
  const navigation = await evaluate(`Array.from(
    document.querySelectorAll('nav[aria-label="Primary navigation"] button')
  ).map((item) => item.textContent.trim()).filter(Boolean)`)
  const chat = await auditScreen('General Chat')
  const desktopScreenshot = await capture('ui-ux-desktop.png')

  await clickButton('Connections', true)
  await clickButton('Explore')
  await waitUntil(
    `document.querySelectorAll('main article').length > 0 ||
      document.querySelector('main [role="status"]') !== null`
  )
  const connections = await evaluate(`(() => {
    const cards = Array.from(document.querySelectorAll('main article'))
    const headings = cards
      .map((card) => card.querySelector('h2, h3')?.textContent?.trim())
      .filter(Boolean)
    const duplicates = headings.filter((heading, index) => headings.indexOf(heading) !== index)
    return {
      heading: document.querySelector('main h1')?.textContent?.trim() || '',
      cardCount: cards.length,
      duplicateCards: [...new Set(duplicates)],
      enabledConnectButtons: Array.from(document.querySelectorAll('main button'))
        .filter((button) => button.textContent.trim() === 'Connect' && !button.disabled)
        .length,
      text: document.querySelector('main')?.innerText?.slice(0, 800) || ''
    }
  })()`)
  const connectionsAudit = await auditScreen('Connections')

  await clickButton('Settings', true)
  await clickButton('Telegram')
  await waitUntil(`!document.querySelector('main')?.innerText.includes('Checking Telegram')`)
  const telegram = await evaluate(`({
    heading: document.querySelector('main h2')?.textContent?.trim() ||
      document.querySelector('main h1')?.textContent?.trim() || '',
    text: document.querySelector('main')?.innerText?.slice(0, 900) || ''
  })`)
  const telegramAudit = await auditScreen('Telegram settings')

  await clickButton('Services')
  const services = await evaluate(`({
    hasRedirect: Array.from(document.querySelectorAll('main button')).some(
      (button) => button.textContent.includes('Open Connections')
    ),
    text: document.querySelector('main')?.innerText?.slice(0, 500) || ''
  })`)
  await clickButton('Open Connections', true)
  const servicesRedirectHeading = await evaluate(
    `document.querySelector('main h1')?.textContent?.trim() || ''`
  )

  await clickButton('Agents', true)
  await clickButton('New agent')
  const agentDialog = await evaluate(`({
    present: Boolean(document.querySelector('[role="dialog"]')),
    text: document.querySelector('[role="dialog"]')?.innerText?.slice(0, 600) || ''
  })`)
  if (agentDialog.present) {
    const canCancel = await evaluate(`Array.from(document.querySelectorAll(
      '[role="dialog"] button'
    )).some((button) => /cancel|close/i.test(
      button.textContent.trim() || button.getAttribute('aria-label') || ''
    ))`)
    if (canCancel) {
      await evaluate(`(() => {
        const button = Array.from(document.querySelectorAll('[role="dialog"] button')).find(
          (item) => /cancel|close/i.test(
            item.textContent.trim() || item.getAttribute('aria-label') || ''
          )
        )
        button.click()
      })()`)
    }
  }

  await setWindowSize(760, 520)
  const compact = {}
  for (const label of ['General Chat', 'Agents', 'Skills', 'Routines', 'Connections', 'Settings']) {
    await clickButton(label, true)
    compact[label] = await auditScreen(label)
  }
  const compactScreenshot = await capture('ui-ux-compact.png')

  await setWindowSize(1120, 780)
  await clickButton('General Chat', true)

  const failures = []
  const expectedNavigation = ['Agents', 'Skills', 'Routines', 'Connections']
  if (core.state !== 'running') failures.push(`Core state is ${core.state}: ${core.error || ''}`)
  if (!ipcProbe.ok) failures.push(`Direct renderer-to-core IPC probe failed: ${ipcProbe.error || ipcProbe.message}`)
  for (const label of expectedNavigation) {
    if (!navigation.some((item) => item.startsWith(label))) {
      failures.push(`Primary navigation is missing ${label}`)
    }
  }
  for (const audit of [chat, connectionsAudit, telegramAudit, ...Object.values(compact)]) {
    if (audit.horizontalOverflow) failures.push(`${audit.name} has horizontal overflow`)
    if (audit.overlay) failures.push(`${audit.name} shows a framework error overlay`)
    if (audit.unlabeledControls.length) {
      failures.push(`${audit.name} has ${audit.unlabeledControls.length} unlabeled controls`)
    }
  }
  if (connections.heading !== 'Connections') failures.push('Connections heading did not render')
  if (connections.duplicateCards.length) {
    failures.push(`Duplicate connection cards: ${connections.duplicateCards.join(', ')}`)
  }
  if (connections.enabledConnectButtons) {
    failures.push('An unverified connection has an enabled Connect button')
  }
  if (!services.hasRedirect || servicesRedirectHeading !== 'Connections') {
    failures.push('Settings → Services does not redirect to Connections')
  }
  if (!telegram.text.includes('BotFather')) failures.push('Telegram setup guidance is incomplete')
  if (!agentDialog.present) failures.push('New agent did not open an accessible dialog')
  if (rendererErrors.length) failures.push(`${rendererErrors.length} renderer error(s) occurred`)

  socket.close()
  const result = {
    core,
    ipcProbe,
    navigation,
    chat,
    connections,
    telegram,
    services,
    servicesRedirectHeading,
    agentDialog,
    compact,
    rendererErrors,
    screenshots: { desktop: desktopScreenshot, compact: compactScreenshot },
    failures
  }
  console.log(JSON.stringify(result, null, 2))
  if (failures.length) process.exit(1)
}

main().catch((error) => {
  console.error(error.stack || error)
  process.exit(1)
})
