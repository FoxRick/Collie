// Live-app regression checks for the sidebar footer (#168), collapsed-rail
// persistence, feedback-dialog focus handling, navigation, search and the
// Settings tab list.
//
// Complements scripts/verify-ui-ux.cjs, which covers core IPC, Connections,
// Telegram, Services and the agent dialog, but does not touch the sidebar
// footer, the collapsed rail, or the Settings tab list.
//
// Usage (the app must be running with a remote debugging port):
//   COLLIE_DEBUG_PORT=9222 node scripts/verify-ui-ux-sidebar-feedback.cjs
//   COLLIE_DEBUG_PORT=9222 node scripts/verify-ui-ux-sidebar-feedback.cjs --submit-live-feedback
//
// Submissions are skipped by default. --submit-live-feedback explicitly enables
// a production POST that stores a real feedback row and sends a real email.
// The older --no-submit flag remains supported and takes precedence.
//
// Every check drives the real renderer with real mouse and key events, so
// focus behaviour is asserted the way a keyboard user experiences it.
// Exit code 0 means every check passed; failures are printed as JSON.

const { mkdirSync, writeFileSync } = require('fs')
const { dirname, resolve } = require('path')

const debugPort = Number(process.env.COLLIE_DEBUG_PORT || 9223)
const submitLiveFeedback = process.argv.includes('--submit-live-feedback') && !process.argv.includes('--no-submit')
const outputDir = resolve(process.env.COLLIE_UI_UX_OUTPUT || '../.local-runtime-logs')
const failures = []
const checks = []

function record(name, ok, detail) {
  checks.push({ name, ok: Boolean(ok), detail })
  if (!ok) {
    failures.push(`${name} -> ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
}

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

const VIRTUAL_KEYS = {
  Escape: { code: 'Escape', vk: 27 },
  Enter: { code: 'Enter', vk: 13 },
  Tab: { code: 'Tab', vk: 9 },
  Backspace: { code: 'Backspace', vk: 8 }
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

  async function waitUntil(expression, attempts = 40) {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (await evaluate(expression).catch(() => false)) return true
      await delay(250)
    }
    return false
  }

  // Real mouse click at the element's centre. A JS .click() would not move
  // focus in Chromium, which is exactly what the focus-return checks need.
  async function rectFor(expression) {
    return await evaluate(`(() => {
      const element = ${expression}
      if (!element) return null
      const rect = element.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return null
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, width: rect.width, height: rect.height }
    })()`)
  }

  async function realClick(expression) {
    const rect = await rectFor(expression)
    if (!rect) throw new Error(`Missing or invisible element: ${expression}`)
    await command('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x: rect.x,
      y: rect.y,
      button: 'none',
      clickCount: 0
    })
    await command('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x: rect.x,
      y: rect.y,
      button: 'left',
      clickCount: 1
    })
    await delay(40)
    await command('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x: rect.x,
      y: rect.y,
      button: 'left',
      clickCount: 1
    })
    await delay(350)
    return rect
  }

  function clickByText(scopeSelector, text) {
    return realClick(
      `Array.from(document.querySelectorAll(${JSON.stringify(scopeSelector)}))
        .find((item) => item.textContent.trim().startsWith(${JSON.stringify(text)}))`
    )
  }

  // Sidebar destinations are spread across three containers (header, primary
  // nav, footer), so resolve them by visible text across every button.
  function clickButtonText(text) {
    return realClick(
      `Array.from(document.querySelectorAll('button'))
        .find((item) => item.textContent.trim().startsWith(${JSON.stringify(text)}))`
    )
  }

  async function pressKey(key) {
    const meta = VIRTUAL_KEYS[key] || { code: key, vk: 0 }
    const base = {
      key,
      code: meta.code,
      windowsVirtualKeyCode: meta.vk,
      nativeVirtualKeyCode: meta.vk
    }
    await command('Input.dispatchKeyEvent', { ...base, type: 'keyDown' })
    await command('Input.dispatchKeyEvent', { ...base, type: 'keyUp' })
    await delay(350)
  }

  async function capture(name) {
    const result = await command('Page.captureScreenshot', { format: 'png', fromSurface: true })
    const path = resolve(outputDir, name)
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, Buffer.from(result.data, 'base64'))
    return path
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

  const footerState = `(() => {
    const rows = Array.from(document.querySelectorAll('.sidebar-footer > button'))
    const aside = document.querySelector('aside')
    return {
      order: rows.map((row) =>
        row.classList.contains('sidebar-collapse-toggle')
          ? 'collapse'
          : row.classList.contains('sidebar-feedback')
            ? 'feedback'
            : row.classList.contains('sidebar-settings')
              ? 'settings'
              : 'unknown'),
      labels: rows.map((row) => row.getAttribute('aria-label') || row.getAttribute('title') || ''),
      collapsed: Boolean(aside && aside.classList.contains('is-collapsed')),
      width: aside ? Math.round(aside.getBoundingClientRect().width) : null,
      toggleLabel: rows[0] ? rows[0].getAttribute('aria-label') : null,
      toggleExpanded: rows[0] ? rows[0].getAttribute('aria-expanded') : null,
      stored: localStorage.getItem('collie.sidebarCollapsed')
    }
  })()`

  const FOOTER_ORDER = ['collapse', 'feedback', 'settings']
  const asideWidth = `(() => { const aside = document.querySelector('aside'); return aside ? Math.round(aside.getBoundingClientRect().width) : null })()`
  const dialogOpen = `Boolean(document.querySelector('dialog[open]'))`
  const focusInTextarea = `document.activeElement && document.activeElement.tagName === 'TEXTAREA'`
  const feedbackTrigger = `document.querySelector('.sidebar-feedback')`
  const collapseToggle = `document.querySelector('.sidebar-collapse-toggle')`
  const searchPanelHidden = `(() => { const panel = document.querySelector('#sidebar-search'); return !panel || panel.hidden })()`

  await command('Runtime.enable')
  await command('Log.enable')
  await command('Page.enable')
  await command('Page.bringToFront')

  let state = null
  const summary = {}
  try {
  const appShell = `Boolean(document.querySelector('nav[aria-label="Primary navigation"]'))`
  if (!(await evaluate(appShell))) {
    // Same preview seeding as scripts/verify-ui-ux.cjs: sessionStorage, never a
    // query string (renderer security treats query-string URLs as untrusted).
    await evaluate(`sessionStorage.setItem('collie.ui-ux-preview', '1')`)
    await command('Page.navigate', { url: page.url })
    await delay(500)
    await command('Runtime.enable')
  }
  record('app shell renders', await waitUntil(appShell), await evaluate(appShell))

  // ------------------------------------------------------------- preflight
  // The checks below assert an expanded rail with no modal open. Normalise the
  // app first: a dialog left open by a previous run (or a rail restored as
  // collapsed from localStorage) otherwise fails checks that are not about that
  // state. Not recorded as a check — it only sets the starting state.
  await pressKey('Escape')
  await evaluate(`localStorage.setItem('collie.sidebarCollapsed', '0')`)
  await command('Page.reload', { ignoreCache: false })
  await delay(700)
  await command('Runtime.enable')
  if (!(await waitUntil(appShell))) console.error('PREFLIGHT: app shell missing after reload')

  // ---------------------------------------------------------------- footer order
  state = await evaluate(footerState)
  record('footer order is collapse, feedback, settings', JSON.stringify(state.order) === JSON.stringify(FOOTER_ORDER), state.order)
  record('footer labels are human readable', state.labels.every((label) => label && !label.includes('.')), state.labels)
  record('sidebar starts expanded at ~288px', !state.collapsed && state.width >= 200, { collapsed: state.collapsed, width: state.width })

  // -------------------------------------------------------------- collapse rail
  await realClick(collapseToggle)
  state = await evaluate(footerState)
  const collapsedWidth = state.width
  record('collapse hides the rail to ~64px', state.collapsed && state.width <= 80, { collapsed: state.collapsed, width: state.width })
  record('footer order survives collapse', JSON.stringify(state.order) === JSON.stringify(FOOTER_ORDER), state.order)
  record('collapse toggle flips its accessible name', state.toggleExpanded === 'false' && /expand/i.test(state.toggleLabel || ''), { label: state.toggleLabel, expanded: state.toggleExpanded })
  record('collapse writes the preference to storage', state.stored === '1', state.stored)
  const collapsedDetail = await evaluate(`(() => {
    const aside = document.querySelector('aside')
    const rect = aside.getBoundingClientRect()
    const footer = document.querySelector('.sidebar-footer').getBoundingClientRect()
    return {
      overflowX: aside.scrollWidth > aside.clientWidth + 1,
      footerInside: footer.left >= rect.left - 1 && footer.right <= rect.right + 1,
      clippedControls: Array.from(aside.querySelectorAll('button')).filter((button) => {
        const box = button.getBoundingClientRect()
        return box.width > 0 && (box.left < rect.left - 1 || box.right > rect.right + 1)
      }).length
    }
  })()`)
  record('collapsed rail has no overflow or clipped controls', !collapsedDetail.overflowX && collapsedDetail.footerInside && collapsedDetail.clippedControls === 0, collapsedDetail)
  const collapsedShot = await capture('sidebar-collapsed-rail.png')

  // ------------------------------------------------- persistence across reload
  await command('Page.navigate', { url: page.url })
  await delay(600)
  await command('Runtime.enable')
  await waitUntil(appShell)
  state = await evaluate(footerState)
  record('collapsed state persists across a reload', state.collapsed && state.width <= 80 && state.stored === '1', { collapsed: state.collapsed, width: state.width, stored: state.stored })
  record('footer order persists across a reload', JSON.stringify(state.order) === JSON.stringify(FOOTER_ORDER), state.order)

  // -------------------------------------------- feedback dialog, collapsed rail
  await realClick(feedbackTrigger)
  record('feedback opens from the collapsed rail', await evaluate(dialogOpen), await evaluate(dialogOpen))
  record('focus lands in the feedback textarea', await evaluate(focusInTextarea), await evaluate(`document.activeElement && document.activeElement.tagName`))
  await pressKey('Escape')
  record('Escape closes the dialog and returns focus to the trigger', !(await evaluate(dialogOpen)) && (await evaluate(`document.activeElement === ${feedbackTrigger}`)), { open: await evaluate(dialogOpen) })
  const dialogShot = await capture('feedback-dialog-collapsed.png')

  // ----------------------------------------------------------- restore expanded
  await realClick(collapseToggle)
  state = await evaluate(footerState)
  record('expand restores the ~288px rail and clears storage', !state.collapsed && state.width >= 200 && state.stored === '0', { collapsed: state.collapsed, width: state.width, stored: state.stored })

  // ------------------------------------------- feedback dialog, expanded rail
  await realClick(feedbackTrigger)
  const expandedDialog = await evaluate(dialogOpen)
  record('feedback opens from the expanded rail', expandedDialog, expandedDialog)
  const cancelRect = await rectFor(
    `Array.from(document.querySelectorAll('dialog button')).find((item) => item.textContent.trim() === 'Cancel')`
  )
  record('feedback dialog offers a Cancel button', Boolean(cancelRect), cancelRect)
  if (cancelRect) {
    await realClick(`Array.from(document.querySelectorAll('dialog button')).find((item) => item.textContent.trim() === 'Cancel')`)
  }
  record('Cancel closes the dialog and returns focus to the trigger', !(await evaluate(dialogOpen)) && (await evaluate(`document.activeElement === ${feedbackTrigger}`)), { open: await evaluate(dialogOpen) })

  // ---------------------- feedback submit must not strand focus on <body>
  // Post-condition pin for the failure path only. The in-flight Send button is
  // disabled, and Chromium drops focus to <body> when the focused control
  // becomes disabled. With a healthy backend the submit succeeds instead, the
  // dialog moves to its sent state, and focus goes to Done — so this check
  // passes whether or not FeedbackDialog reclaims focus. The mechanism-level
  // pin (focus is reclaimed to the textarea after a *failed* submit) lives in
  // FeedbackDialog.dom.test.tsx. Live POSTs require --submit-live-feedback.
  if (!submitLiveFeedback) {
    console.error('SKIP: live feedback submit (requires --submit-live-feedback; --no-submit overrides it)')
  } else {
    await realClick(feedbackTrigger)
    await waitUntil(focusInTextarea)
    await command('Input.insertText', { text: 'Automated UI regression probe — please ignore.' })
    const typedLength = await evaluate(`document.querySelector('#feedback-message').value.length`)
    await realClick(
      `Array.from(document.querySelectorAll('dialog button')).find((item) => item.textContent.trim() === 'Send')`
    )
    const settled = await waitUntil(
      `Boolean(document.querySelector('dialog [role=alert], dialog [role=status]'))`,
      120
    )
    record('feedback submit settles into a success or failure state', settled, settled)
    const afterSubmit = await evaluate(`(() => {
    const dialog = document.querySelector('dialog[open]')
    const active = document.activeElement
    const draft = document.querySelector('#feedback-message')
    return {
      active: active ? active.tagName : null,
      insideDialog: Boolean(dialog && active && dialog.contains(active)),
      alert: Boolean(document.querySelector('dialog [role=alert]')),
      status: Boolean(document.querySelector('dialog [role=status]')),
      draftLength: draft ? draft.value.length : null
    }
  })()`)
    record('focus stays inside the dialog after the submit settles', afterSubmit.insideDialog, afterSubmit)
    record('a failed submit keeps the draft in place', !afterSubmit.alert || afterSubmit.draftLength === typedLength, { ...afterSubmit, typedLength })
    await pressKey('Escape')
    await waitUntil(`!(${dialogOpen})`)
  }

  // ------------------------------------------------------------------- search
  const searchToggle = `Array.from(document.querySelectorAll('button[aria-label="Search conversations"]')).find((item) => item.getBoundingClientRect().width > 0)`
  const unfilteredCount = await evaluate(`document.querySelectorAll('.conversation-row').length`)
  await realClick(searchToggle)
  record('search panel opens and takes focus', await waitUntil(`!(${searchPanelHidden})`) && (await evaluate(`document.activeElement && document.activeElement.tagName === 'INPUT'`)), await evaluate(`document.activeElement && document.activeElement.tagName`))
  // A fresh query avoids assumptions about titles or how many chats exist.
  const searchQuery = `collie-no-match-${require('node:crypto').randomUUID()}`
  await command('Input.insertText', { text: searchQuery })
  await delay(800)
  // Search also matches content. A unique no-match query should yield no rows,
  // including when the app starts with an empty history.
  const filtered = await evaluate(`(() => {
    const input = document.querySelector('input[aria-label="Search conversations"]') ||
      document.querySelector('#sidebar-search input') ||
      document.querySelector('input[type="search"]')
    const rows = Array.from(document.querySelectorAll('.conversation-row'))
    return { query: input ? input.value : null, count: rows.length, texts: rows.map((row) => row.textContent.trim().slice(0, 60)) }
  })()`)
  record('search accepts a no-match query and shows no conversations', filtered.query === searchQuery && filtered.count === 0, { ...filtered, unfilteredCount })
  await pressKey('Escape')
  record('Escape closes search and returns focus to the toggle', (await evaluate(searchPanelHidden)) && (await evaluate(`document.activeElement === ${searchToggle}`)), { hidden: await evaluate(searchPanelHidden) })

  // -------------------------------------------------------------- navigation
  const primaryNavLabels = await evaluate(`Array.from(document.querySelectorAll('nav[aria-label="Primary navigation"] button')).map((item) => item.textContent.trim()).filter(Boolean)`)
  record('primary navigation lists its destinations', primaryNavLabels.length >= 4, primaryNavLabels)
  // New chat and General Chat live outside the primary nav and Settings lives
  // in the footer, so the sweep covers all seven user-visible destinations.
  const navigation = {}
  for (const label of ['New chat', ...primaryNavLabels, 'General Chat', 'Settings']) {
    try {
      // Start chat checks from Settings so a no-op click cannot pass merely
      // because the app was already displaying the chat screen.
      if (label === 'New chat' || label === 'General Chat') await clickButtonText('Settings')
      await clickButtonText(label)
      const audit = await evaluate(`(() => {
        const main = document.querySelector('main')
        return {
          heading: (main && (main.querySelector('h1') || main.querySelector('h2'))?.textContent.trim()) || '',
          currentDestinations: Array.from(document.querySelectorAll('aside button[aria-current="page"]')).map((item) => item.textContent.trim()),
          chatVisible: Boolean(document.querySelector('main .conversation-panel')),
          textLength: main ? main.innerText.trim().length : 0,
          horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
        }
      })()`)
      navigation[label] = audit
      const correctDestination = label === 'New chat'
        ? audit.chatVisible && audit.heading === 'New conversation'
        : label === 'General Chat'
          ? audit.chatVisible && audit.currentDestinations.includes(label)
          : audit.heading === label && audit.currentDestinations.includes(label)
      record(`navigation to ${label} renders content`, correctDestination && audit.textLength > 20 && !audit.horizontalOverflow, audit)
    } catch (error) {
      navigation[label] = { error: error.message }
      record(`navigation to ${label} renders content`, false, error.message)
    }
  }

  // ---------------------------------------------------------- settings tabs
  await clickButtonText('Settings')
  const settingsTabs = await evaluate(`Array.from(document.querySelectorAll('.settings-nav-item')).map((item) => item.textContent.trim()).filter(Boolean)`)
  record('Settings lists its tabs', settingsTabs.length >= 8, settingsTabs)
  const settingsAudit = {}
  for (const tab of settingsTabs) {
    try {
      await clickByText('.settings-nav-item', tab)
      const audit = await evaluate(`(() => {
        const main = document.querySelector('main')
        return {
          textLength: main ? main.innerText.trim().length : 0,
          active: Array.from(document.querySelectorAll('.settings-nav-item.is-active')).map((item) => item.textContent.trim()),
          horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
        }
      })()`)
      settingsAudit[tab] = audit
      record(`Settings tab ${tab} renders content`, audit.textLength > 20 && audit.active.includes(tab) && !audit.horizontalOverflow, audit)
    } catch (error) {
      settingsAudit[tab] = { error: error.message }
      record(`Settings tab ${tab} renders content`, false, error.message)
    }
  }

  // ------------------------------------------------------- layout invariants
  await clickButtonText('General Chat')
  const overlap = await evaluate(`(() => {
    const list = document.querySelector('.sidebar-conversation-nav')
    const footer = document.querySelector('.sidebar-footer')
    if (!list || !footer) return null
    const listRect = list.getBoundingClientRect()
    const footerRect = footer.getBoundingClientRect()
    return { listBottom: Math.round(listRect.bottom), footerTop: Math.round(footerRect.top), overlapPx: Math.round(listRect.bottom - footerRect.top) }
  })()`)
  record('conversation list does not overlap the footer', overlap && overlap.overlapPx <= 1, overlap)

  await setWindowSize(760, 520)
  const compact = await evaluate(`(() => {
    const aside = document.querySelector('aside')
    const footer = document.querySelector('.sidebar-footer')
    return {
      asideWidth: aside ? Math.round(aside.getBoundingClientRect().width) : null,
      footerVisible: footer ? footer.getBoundingClientRect().bottom <= innerHeight + 1 : false,
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    }
  })()`)
  record('minimum window size keeps the footer on screen without overflow', compact.footerVisible && !compact.horizontalOverflow, compact)
  const compactShot = await capture('sidebar-compact.png')
  await setWindowSize(1120, 780)

  record('no renderer errors during the sweep', rendererErrors.length === 0, rendererErrors.slice(0, 5))
  Object.assign(summary, {
    footer: state,
    collapsedWidth,
    collapsedDetail,
    filteredSearch: filtered,
    navigation,
    settingsTabs: settingsAudit,
    overlap,
    compact,
    screenshots: { collapsedShot, dialogShot, compactShot }
  })
  } catch (error) {
    record('suite ran to completion', false, error.stack || error.message)
  }

  socket.close()
  const result = { ...summary, rendererErrors, checks, failures }
  console.log(JSON.stringify(result, null, 2))
  if (failures.length) process.exit(1)
}

main().catch((error) => {
  console.error(error.stack || error)
  process.exit(1)
})
