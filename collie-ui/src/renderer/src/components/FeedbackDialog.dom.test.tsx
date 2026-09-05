// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import FeedbackDialog from './FeedbackDialog'

let root: Root
const submitFeedback = vi.fn()
const onClose = vi.fn()
beforeAll(() => {
  ;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  HTMLDialogElement.prototype.showModal = function (): void { this.setAttribute('open', '') }
})
beforeEach(() => {
  submitFeedback.mockReset()
  onClose.mockReset()
  Object.defineProperty(window, 'collie', { value: { submitFeedback }, configurable: true })
  const container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  act(() => root.render(<FeedbackDialog onClose={onClose} />))
})
afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})
function input(value: string): void {
  const textarea = document.querySelector('textarea')!
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, value)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
async function send(): Promise<void> {
  await act(async () => { document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
}

it('offers only a message field and prevents blank submission', async () => {
  expect(document.querySelector('input')).toBeNull()
  expect(document.querySelector('textarea')!.maxLength).toBe(4000)
  input('   ')
  expect(document.querySelector<HTMLButtonElement>('[type=submit]')!.disabled).toBe(true)
  await send()
  expect(submitFeedback).not.toHaveBeenCalled()
})

it('keeps failed drafts and retries with the same ID before confirming success', async () => {
  submitFeedback.mockResolvedValueOnce({ ok: false, error: 'unavailable' }).mockResolvedValueOnce({ ok: true })
  input('Please add keyboard shortcuts.')
  await send()
  expect(document.querySelector('textarea')!.value).toBe('Please add keyboard shortcuts.')
  expect(document.querySelector('[role=alert]')!.textContent).toContain('try again')
  expect(document.querySelector('[role=status]')).toBeNull()
  await send()
  expect(submitFeedback.mock.calls[0][0]).toEqual(submitFeedback.mock.calls[1][0])
  expect(Object.keys(submitFeedback.mock.calls[0][0]).sort()).toEqual(['id', 'message'])
  expect(document.querySelector('[role=status]')!.textContent).toContain('has been sent')
  expect(document.querySelector('textarea')).toBeNull()
  expect(document.activeElement?.textContent).toBe('Done')
})

it('uses a new submission ID when the failed draft is edited', async () => {
  submitFeedback.mockResolvedValue({ ok: false, error: 'rate_limited' })
  input('First message')
  await send()
  expect(document.querySelector('[role=alert]')!.textContent).toContain('wait a minute')
  input('Changed message')
  await send()
  expect(submitFeedback.mock.calls[0][0].id).not.toBe(submitFeedback.mock.calls[1][0].id)
})

it('blocks duplicate sends and Escape while pending', async () => {
  let resolve!: (value: { ok: true }) => void
  submitFeedback.mockReturnValue(new Promise((done) => { resolve = done }))
  input('Hello team')
  await send()
  await send()
  expect(submitFeedback).toHaveBeenCalledTimes(1)
  expect(document.querySelector('textarea')!.disabled).toBe(true)
  act(() => { document.querySelector('dialog')!.dispatchEvent(new Event('cancel', { cancelable: true })) })
  expect(onClose).not.toHaveBeenCalled()
  await act(async () => { resolve({ ok: true }) })
  act(() => { document.querySelector('dialog')!.dispatchEvent(new Event('cancel', { cancelable: true })) })
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('preserves text when the bridge rejects', async () => {
  submitFeedback.mockRejectedValue(new Error('offline'))
  input('Still here')
  await send()
  expect(document.querySelector('textarea')!.value).toBe('Still here')
  expect(document.querySelector('[role=alert]')).not.toBeNull()
})
