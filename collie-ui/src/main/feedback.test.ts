import { afterEach, describe, expect, it, vi } from 'vitest'
import { FEEDBACK_URL, submitFeedback } from './feedback'

const submission = { id: 'b9d6f334-3d32-4d57-87d3-8ed1056ce4b0', message: '  Please add shortcuts.  ' }
afterEach(() => vi.unstubAllGlobals())

describe('feedback delivery', () => {
  it('sends only the ID and trimmed message to the fixed endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }))
    vi.stubGlobal('fetch', fetcher)
    expect(await submitFeedback({ ...submission, email: 'private@example.com', logs: 'private' })).toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledWith(FEEDBACK_URL, expect.objectContaining({
      method: 'POST', redirect: 'error', credentials: 'omit',
      body: JSON.stringify({ id: submission.id, message: submission.message.trim() })
    }))
  })

  it.each([null, {}, { ...submission, id: '../bad' }, { ...submission, message: '  ' },
    { ...submission, message: 'x'.repeat(4001) }])('rejects malformed IPC payloads', async (value) => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    expect(await submitFeedback(value)).toEqual({ ok: false, error: 'invalid' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it.each([429, 500, 404])('handles HTTP %s without claiming delivery', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status })))
    expect(await submitFeedback(submission)).toEqual({
      ok: false, error: status === 429 ? 'rate_limited' : 'unavailable'
    })
  })

  it('rejects HTML fallback and network failures, and permits retry', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(new Response('<html>not deployed</html>'))
      .mockResolvedValueOnce(Response.json({ ok: false }))
      .mockResolvedValueOnce(Response.json({ ok: true }))
    vi.stubGlobal('fetch', fetcher)
    for (let i = 0; i < 3; i++) expect(await submitFeedback(submission)).toEqual({ ok: false, error: 'unavailable' })
    expect(await submitFeedback(submission)).toEqual({ ok: true })
  })

  it('prevents concurrent submissions', async () => {
    let finish!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve })))
    const first = submitFeedback(submission)
    expect(await submitFeedback(submission)).toEqual({ ok: false, error: 'rate_limited' })
    finish(Response.json({ ok: true }))
    expect(await first).toEqual({ ok: true })
  })
})
