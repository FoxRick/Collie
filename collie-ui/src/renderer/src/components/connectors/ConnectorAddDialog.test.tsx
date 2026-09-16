// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import ConnectorAddDialog from './ConnectorAddDialog'

let root: Root
const onClose = vi.fn()
const onValidate = vi.fn()
const onChooseImport = vi.fn()
const onSubmit = vi.fn()
const onDiscardImportSecrets = vi.fn()

beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  onClose.mockReset(); onValidate.mockReset(); onChooseImport.mockReset(); onSubmit.mockReset(); onDiscardImportSecrets.mockReset(); onDiscardImportSecrets.mockResolvedValue(undefined)
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<ConnectorAddDialog busy={false} onClose={onClose} onValidate={onValidate} onChooseImport={onChooseImport} onDiscardImportSecrets={onDiscardImportSecrets} onSubmit={onSubmit} />))
})

afterEach(() => { act(() => root.unmount()); document.body.innerHTML = '' })

function click(text: string): void {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes(text))
  if (!button) throw new Error(`Missing button: ${text}`)
  act(() => button.click())
}

function change(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(element.constructor.prototype, 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

describe('ConnectorAddDialog', () => {
  it('clears a token before protected submission settles and closes on Escape', async () => {
    click('Connect using a link')
    const inputs = [...document.querySelectorAll('input')]
    change(inputs.find((item) => item.placeholder === 'My workspace')!, 'Private notes')
    change(inputs.find((item) => item.type === 'url')!, 'https://notes.example/mcp')
    change(document.querySelectorAll('select')[1], 'token')
    onValidate.mockResolvedValue({ valid: true, preview: { name: 'Private notes', endpoint: 'https://notes.example/mcp', transport: 'streamable_http', auth_strategy: 'token', allow_private_network: false, has_secret: false, requires_secret: true }, warnings: [], errors: [] })
    await act(async () => click('Review connection'))
    const password = document.querySelector<HTMLInputElement>('input[type=password]')!
    change(password, 'never-render-this-secret')
    onSubmit.mockReturnValue(new Promise(() => undefined))
    act(() => click('Accept and continue'))
    expect(password.value).toBe('')
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ endpoint: 'https://notes.example/mcp' }), { token: 'never-render-this-secret' }, undefined)
    expect(document.body.textContent).not.toContain('never-render-this-secret')
    act(() => document.querySelector('[role=dialog]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows every unsupported import option and never renders imported secrets', async () => {
    click('Import a connection file')
    onChooseImport.mockResolvedValue({
      definitions: [{ key: 'remote', name: 'Remote', secret_handle: 'opaque-handle', definition: { name: 'Remote', endpoint: 'https://example.com/mcp', transport: 'streamable_http', auth_strategy: 'headers' }, preview: { name: 'Remote', endpoint: 'https://example.com/mcp', transport: 'streamable_http', auth_strategy: 'headers', has_secret: true, requires_secret: true } }],
      unsupported: [{ path: 'mcpServers.local.command', reason: 'Local commands are not supported in this release.' }, { path: 'mcpServers.remote.env.DEBUG', reason: 'Environment fields are ignored.' }],
      warnings: []
    })
    await act(async () => click('Choose a JSON connection file'))
    expect(document.body.textContent).toContain('mcpServers.local.command')
    expect(document.body.textContent).toContain('mcpServers.remote.env.DEBUG')
    expect(document.body.textContent).toContain('Secret')
    expect(document.body.textContent).toContain('value hidden')
    expect(document.body.textContent).not.toContain('super-secret-value')
    click('Close')
    expect(onDiscardImportSecrets).toHaveBeenCalledWith(['opaque-handle'])
  })

  it('keeps keyboard focus inside the modal when tabbing', () => {
    const dialog = document.querySelector<HTMLElement>('[role=dialog]')!
    const buttons = [...dialog.querySelectorAll<HTMLButtonElement>('button:not([disabled])')]
    buttons.at(-1)!.focus()
    act(() => buttons.at(-1)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })))
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(buttons[0])
  })

  it('keeps an actionable authentication error inside the open dialog', async () => {
    click('Import a connection file')
    onChooseImport.mockResolvedValue({ definitions: [{ key: 'remote', name: 'Remote', secret_handle: 'one-use-handle', definition: { name: 'Remote', endpoint: 'https://example.com/mcp', auth_strategy: 'token' }, preview: { name: 'Remote', endpoint: 'https://example.com/mcp', transport: 'streamable_http', auth_strategy: 'token', has_secret: true, requires_secret: true } }], unsupported: [], warnings: [] })
    await act(async () => click('Choose a JSON connection file'))
    onSubmit.mockRejectedValue(new Error('A token is required.'))
    await act(async () => click('Accept and continue'))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: 'Remote' }), undefined, 'one-use-handle')
    expect(document.querySelector('[role=dialog]')).not.toBeNull()
    expect(document.querySelector('[role=alert]')?.textContent).toContain('A token is required.')
    expect(document.querySelector('[role=alert]')?.textContent).toContain('Choose the file again')
  })
})
