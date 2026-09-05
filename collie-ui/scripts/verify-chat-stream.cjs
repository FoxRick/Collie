// Run after npm run build: electron scripts/verify-chat-stream.cjs
// Exercises the actual renderer with synthetic core events, never user data.
const { app, BrowserWindow } = require('electron')
const { mkdtempSync, writeFileSync, mkdirSync, rmSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join, resolve } = require('node:path')
const assert = require('node:assert/strict')
const temporary = mkdtempSync(join(tmpdir(), 'collie-chat-qa-'))
app.setPath('userData', join(temporary, 'profile'))
const preload = join(temporary, 'preload.cjs')
writeFileSync(preload, `
  sessionStorage.setItem('collie.ui-ux-preview', '1')
  let listener = () => {}
  window.chatFixture = { emit: event => listener(event) }
  window.collie = {
    onCoreEvent: callback => { listener = callback; return () => {} },
    coreState: async () => ({}),
    coreSend: async frame => {
      if (frame.type === 'chat') {
        setTimeout(() => listener({type: 'message', conversation_id: 'qa', message: {
          id: 'user', conversation_id: 'qa', role: 'user', content: frame.content,
          created_at: '2026-09-05T00:00:00Z'
        }}), 0)
        return {conversation_id: 'qa'}
      }
      return { conversations: [], messages: [], settings: {}, commands: [], agents: [],
        skills: [], approvals: [], things: [], task: null, active_agents: [], recent_agents: [] }
    },
    updateActiveWork: async () => {}, petCommand: async () => {},
    storedSecretCount: async () => 0, onNavigate: () => () => {},
    onUpdateState: () => () => {}, getUpdateState: async () => ({})
  }
`)
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 1120, height: 780, show: false,
    webPreferences: { preload, contextIsolation: false, sandbox: false, backgroundThrottling: false, offscreen: true } })
  const errors = []
  window.webContents.on('console-message', (event) => {
    if (event.level === 'error') { errors.push(event.message); console.error(event.message) }
  })
  const evaluate = code => window.webContents.executeJavaScript(code)
  const emit = event => evaluate(`window.chatFixture.emit(${JSON.stringify(event)})`)
  const output = process.env.COLLIE_CHAT_QA_OUTPUT
  async function screenshot(name) {
    if (!output) return
    mkdirSync(resolve(output), { recursive: true })
    writeFileSync(join(resolve(output), name + '.png'), (await window.webContents.capturePage()).toPNG())
  }
  try {
    await window.loadFile(join(__dirname, '../out/renderer/index.html'))
    await wait(600)
    await evaluate(`(() => {
      const input = document.querySelector('textarea')
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, 'Explain a calm chat experience')
      input.dispatchEvent(new Event('input', {bubbles: true}))
    })()`)
    await wait(50)
    await evaluate(`document.querySelector('button[aria-label="Send message"]').click()`)
    await wait(100)
    await emit({ type: 'thinking', conversation_id: 'qa', state: 'processing', phrase: 'Thinking through your task…', pet_animation: 'working' })
    await wait(100)
    await screenshot('thinking')
    let answer = '## A calmer conversation\n\n' + 'Keep the answer readable and the composer responsive. '.repeat(170)
    await emit({ type: 'delta', conversation_id: 'qa', text: answer })
    await wait(200)
    const streamedCharacters = await evaluate(`document.querySelector('[role="log"]').textContent.length`)
    await screenshot('streaming')
    if (!process.env.COLLIE_CHAT_QA_BASELINE) {
      await evaluate(`(() => {
        const transcript = document.querySelector('.chat-transcript')
        transcript.dispatchEvent(new WheelEvent('wheel', { deltaY: -1000, bubbles: true }))
        transcript.scrollTop = 0
        transcript.dispatchEvent(new Event('scroll'))
      })()`)
      await wait(50)
      const extra = '\n\nKeep the reader in control.'
      answer += extra
      await emit({ type: 'delta', conversation_id: 'qa', text: extra })
      await evaluate(`(() => {
        const input = document.querySelector('textarea')
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, 'Keep this draft while streaming')
        input.dispatchEvent(new Event('input', {bubbles: true}))
      })()`)
      await wait(100)
      assert.equal(await evaluate(`document.querySelector('.chat-transcript').scrollTop`), 0, 'Streaming must respect scroll-back')
      assert.equal(await evaluate(`document.querySelector('.composer-send').disabled`), false, 'Composer must accept input while streaming')
      const jumpVisible = await evaluate(`(() => {
        const button = document.querySelector('.chat-jump-latest')
        const rect = button?.getBoundingClientRect()
        return !!rect && rect.top > 0 && rect.bottom < innerHeight
      })()`)
      assert.ok(jumpVisible, 'Jump to latest must be visible while reading older text')
      await screenshot('scroll-back')
    }
    const started = Date.now()
    await emit({ type: 'message', conversation_id: 'qa', message: {
      id: 'answer', conversation_id: 'qa', role: 'assistant', content: answer, created_at: '2026-09-05T00:00:01Z'
    } })
    await emit({ type: 'thinking', conversation_id: 'qa', state: 'done', phrase: 'All done!', pet_animation: 'happy' })
    await wait(100)
    const finalCharacters = await evaluate(`document.querySelector('[role="log"]').textContent.length`)
    await screenshot('completed')
    console.log(JSON.stringify({ answerCharacters: answer.length, streamedCharacters, finalCharacters, completionCheckMs: Date.now() - started, errors }))
    if (!process.env.COLLIE_CHAT_QA_BASELINE) {
      assert.ok(finalCharacters >= answer.length - 5, 'Final answer must render without a reveal backlog')
      assert.equal(await evaluate(`document.querySelectorAll('.collie-thinking').length`), 0)
      await evaluate(`document.querySelector('.chat-jump-latest').click()`)
      await wait(50)
      assert.ok(await evaluate(`(() => { const e = document.querySelector('.chat-transcript'); return e.scrollHeight - e.scrollTop - e.clientHeight < 96 })()`))
      assert.equal(await evaluate(`document.querySelector('textarea').value`), 'Keep this draft while streaming')
      // Inspect a compact mixed-Markdown answer at desktop and narrow widths.
      await emit({ type: 'message', conversation_id: 'qa', message: {
        id: 'answer', conversation_id: 'qa', role: 'assistant',
        content: '## A calmer conversation\n\nHere is what changed:\n\n- **Faster answers:** completed text appears immediately.\n- **Clear progress:** a compact status shows what is happening.\n- **Your place stays put:** scroll back, then use Jump to latest.\n\nYou can keep typing while Collie works.',
        created_at: '2026-09-05T00:00:01Z'
      } })
      await wait(500)
      await screenshot('desktop-dark')
      await evaluate(`document.documentElement.classList.remove('dark')`)
      window.setSize(860, 720)
      await wait(200)
      await screenshot('narrow-light')
      assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), 'No horizontal page overflow')
      await emit({ type: 'thinking', conversation_id: 'qa', state: 'processing', phrase: 'Checking the next step…', pet_animation: 'working' })
      await emit({ type: 'delta', conversation_id: 'qa', text: 'A second response '.repeat(300) })
      await wait(50)
      await evaluate(`document.querySelector('button[aria-label="Stop response"]').click()`)
      await wait(50)
      const stopped = await evaluate(`document.querySelector('[role="log"]').textContent`)
      await wait(200)
      assert.equal(await evaluate(`document.querySelector('[role="log"]').textContent`), stopped, 'Stop must cancel the presentation timer')
      assert.equal(await evaluate(`document.querySelector('button[aria-label="Stop response"]')`), null)
      await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'New chat').click()`)
      await wait(100)
      await emit({ type: 'delta', conversation_id: 'qa', text: 'Background text must stay out.' })
      await wait(100)
      assert.equal(await evaluate(`document.querySelector('[role="log"]').textContent`), '')
      assert.deepEqual(errors, [])
      console.log('PASS: completion, scroll-back, jump to latest, typing, stop, conversation isolation, dark/light layouts')
    }
  } finally {
    window.destroy()
  }
}).then(() => app.exit(0), error => { console.error(error); app.exit(1) })
process.on('exit', () => { try { rmSync(temporary, { recursive: true, force: true }) } catch {} })

