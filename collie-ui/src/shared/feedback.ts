export const FEEDBACK_MAX_LENGTH = 4000

export interface FeedbackSubmission {
  id: string
  message: string
}

export type FeedbackResult =
  | { ok: true }
  | { ok: false; error: 'invalid' | 'rate_limited' | 'unavailable' }

export function validFeedback(value: unknown): value is FeedbackSubmission {
  if (!value || typeof value !== 'object') return false
  const { id, message } = value as Partial<FeedbackSubmission>
  return typeof id === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id) &&
    typeof message === 'string' && message.trim().length > 0 &&
    message.length <= FEEDBACK_MAX_LENGTH
}
