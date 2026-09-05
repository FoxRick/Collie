import { validFeedback, type FeedbackResult } from '../shared/feedback'

// Fixed destination: the renderer cannot supply URLs, recipients, or credentials.
export const FEEDBACK_URL = 'https://feedback.heycollie.com/api/feedback'
let sending = false

export async function submitFeedback(value: unknown): Promise<FeedbackResult> {
  if (!validFeedback(value)) return { ok: false, error: 'invalid' }
  if (sending) return { ok: false, error: 'rate_limited' }
  sending = true
  try {
    const response = await fetch(FEEDBACK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: value.id, message: value.message.trim() }),
      signal: AbortSignal.timeout(25_000),
      redirect: 'error',
      credentials: 'omit'
    })
    if (response.status === 429) return { ok: false, error: 'rate_limited' }
    if (!response.ok) return { ok: false, error: 'unavailable' }
    const result = await response.json() as { ok?: unknown }
    return result.ok === true ? { ok: true } : { ok: false, error: 'unavailable' }
  } catch {
    return { ok: false, error: 'unavailable' }
  } finally {
    sending = false
  }
}
