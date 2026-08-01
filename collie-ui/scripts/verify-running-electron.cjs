const debugPort = Number(process.env.COLLIE_DEBUG_PORT || 9223)

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function target() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
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
  return await new Promise((resolve, reject) => {
    const socket = new WebSocket(url)
    socket.addEventListener('open', () => resolve(socket), { once: true })
    socket.addEventListener('error', () => reject(new Error('DevTools WebSocket failed.')), {
      once: true
    })
  })
}

async function main() {
  const page = await target()
  const socket = await connect(page.webSocketDebuggerUrl)
  let sequence = 0
  const pending = new Map()

  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data))
    if (!message.id || !pending.has(message.id)) return
    const { resolve, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(message.error.message))
    else resolve(message.result)
  })

  function command(method, params = {}) {
    const id = ++sequence
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
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

  await command('Runtime.enable')
  const core = await evaluate(`(async () => {
    const { token: _token, ...safeCore } = await window.collie.coreState()
    return safeCore
  })()`)
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const settled = await evaluate(`({
      app: Boolean(document.querySelector('nav[aria-label="Primary navigation"]')) &&
        Array.from(document.querySelectorAll('button')).some(
          (item) => item.textContent.trim().startsWith('General Chat')
        ),
      welcome: document.body.innerText.includes('I have a ChatGPT subscription')
    })`).catch(() => ({ app: false, welcome: false }))
    if (settled.app || settled.welcome) break
    await delay(250)
  }
  const welcome = await evaluate(`({
    appVisible: Boolean(document.querySelector('nav[aria-label="Primary navigation"]')) &&
      Array.from(document.querySelectorAll('button')).some(
        (item) => item.textContent.trim().startsWith('General Chat')
      ),
    visible: document.body.innerText.includes('I have a ChatGPT subscription'),
    text: document.querySelector('main')?.innerText?.slice(0, 500) ||
      document.body.innerText.slice(0, 500)
  })`)
  if (!welcome.appVisible) {
    await command('Page.enable')
    const previewUrl = new URL(page.url)
    previewUrl.searchParams.set('preview', '1')
    await command('Page.navigate', { url: previewUrl.toString() })
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const ready = await evaluate(`
        Boolean(document.querySelector('nav[aria-label="Primary navigation"]')) &&
        Array.from(document.querySelectorAll('button')).some(
          (item) => item.textContent.trim().startsWith('General Chat')
        )
      `).catch(() => false)
      if (ready) {
        welcome.appVisible = true
        break
      }
      await delay(250)
    }
  }
  await evaluate(`(async () => {
    const button = Array.from(document.querySelectorAll('button')).find(
      (item) => item.textContent.trim().startsWith('General Chat')
    )
    if (!button) throw new Error('Missing Chat button')
    button.click()
    await new Promise((resolve) => setTimeout(resolve, 300))
  })()`)
  const initial = await evaluate(`({
    title: document.title,
    buttons: Array.from(document.querySelectorAll('button')).map((button) =>
      (button.textContent || button.getAttribute('aria-label') || '').trim()
    ).filter(Boolean),
    hasAttachmentButton: Boolean(document.querySelector('button[aria-label="Attach files"]')),
    hasChatInput: Boolean(document.querySelector('textarea'))
  })`)

  let liveChat = null
  if (process.env.COLLIE_LIVE_CHAT === '1') {
    const marker = 'COLLIE_MVP_LIVE_OK'
    const assistantCount = await evaluate(
      `document.querySelectorAll('.message-row--assistant').length`
    )
    await evaluate(`(() => {
      const textarea = document.querySelector('textarea')
      if (!textarea) throw new Error('Missing chat input for live verification')
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        'value'
      ).set
      setter.call(
        textarea,
        ${JSON.stringify(`Release smoke test: reply with exactly ${marker} and nothing else.`)}
      )
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
      const send = document.querySelector('button[aria-label="Send message"]')
      if (!send) throw new Error('Missing send button for live verification')
      send.click()
    })()`)

    for (let attempt = 0; attempt < 180; attempt += 1) {
      liveChat = await evaluate(`(() => {
        const rows = Array.from(document.querySelectorAll('.message-row--assistant'))
        const latest = rows.at(-1)?.innerText?.trim() || ''
        return {
          assistantCount: rows.length,
          content: latest,
          complete: rows.length > ${assistantCount} &&
            !document.querySelector('button[aria-label="Stop response"]')
        }
      })()`).catch(() => null)
      if (liveChat?.complete) break
      await delay(500)
    }
    liveChat = { ...liveChat, marker, matched: liveChat?.content === marker }
  }

  const views = {}
  for (const label of ['Agents', 'Skills', 'Routines', 'Connections']) {
    views[label] = await evaluate(`(async () => {
      const button = Array.from(document.querySelectorAll('button')).find(
        (item) => item.textContent.trim().startsWith(${JSON.stringify(label)})
      )
      if (!button) throw new Error('Missing navigation button: ${label}')
      button.click()
      await new Promise((resolve) => setTimeout(resolve, 300))
      return {
        heading: document.querySelector('main h1')?.textContent?.trim() || '',
        text: document.querySelector('main')?.innerText?.slice(0, 500) || ''
      }
    })()`)
  }

  views.Settings = await evaluate(`(async () => {
    const button = Array.from(document.querySelectorAll('button')).find(
      (item) => item.textContent.trim().startsWith('Settings')
    )
    if (!button) throw new Error('Missing Settings button')
    button.click()
    await new Promise((resolve) => setTimeout(resolve, 300))
    return {
      tabs: Array.from(document.querySelectorAll('.settings-nav button')).map(
        (item) => item.textContent.trim()
      ),
      text: document.querySelector('main')?.innerText?.slice(0, 500) || ''
    }
  })()`)

  socket.close()

  const failures = []
  if (core.state !== 'running') failures.push(`core state is ${core.state}: ${core.error || 'no detail'}`)
  if (!welcome.appVisible && !welcome.visible) failures.push('app never left its loading screen')
  if (welcome.visible && !welcome.text) failures.push('welcome screen rendered without content')
  if (!initial.hasAttachmentButton) failures.push('attachment button is missing')
  if (!initial.hasChatInput) failures.push('chat input is missing')
  if (process.env.COLLIE_LIVE_CHAT === '1' && !liveChat?.complete) {
    failures.push('live chat did not complete')
  }
  if (process.env.COLLIE_LIVE_CHAT === '1' && !liveChat?.matched) {
    failures.push(`live chat returned unexpected content: ${liveChat?.content || 'empty'}`)
  }
  for (const label of ['Agents', 'Skills', 'Routines', 'Connections']) {
    if (views[label].heading !== label) failures.push(`${label} heading did not render`)
  }
  if (!views.Settings.tabs?.length) failures.push('Settings tabs did not render')
  if (!views.Settings.tabs?.includes('Telegram')) {
    failures.push('Telegram is missing from Settings')
  }

  console.log(JSON.stringify({ core, welcome, initial, liveChat, views, failures }, null, 2))
  if (failures.length) process.exit(1)
}

main().catch((error) => {
  console.error(error.stack || error)
  process.exit(1)
})
