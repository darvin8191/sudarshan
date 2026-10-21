import axios from 'axios'

/**
 * What the answer to an exit or a cancel says about the order (M6.9). Only an
 * answer from the app itself can say it was refused. No answer at all (the
 * network dropped, the request timed out, a gateway in between answered, or
 * the server broke without saying why) leaves the order possibly sent, which
 * is "not confirmed", never "failed": a trader told it failed sends it again,
 * and a second exit sized from a book that has not caught up reverses the
 * position instead of closing it.
 */

/** Answers from something between the phone and the app, not the app. */
const GATEWAY = new Set([502, 503, 504])

/** Whether a body is the app's own answer: a JSON object with its status word. */
export function appAnswered(body: unknown): body is { status: string; message?: unknown } {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as { status?: unknown }).status === 'string'
  )
}

/**
 * What a send that threw says: `refused` when the app answered no, or when
 * the request never left (the security token step before it failed);
 * `unconfirmed` when no answer of the app's own came back.
 */
export function thrownOutcome(error: unknown): 'refused' | 'unconfirmed' {
  if (!axios.isAxiosError(error)) return 'refused'
  const response = error.response
  if (!response) return 'unconfirmed'
  if (GATEWAY.has(response.status)) return 'unconfirmed'
  if (response.status >= 500 && !appAnswered(response.data)) return 'unconfirmed'
  return 'refused'
}

/** The app's own reason for a refusal, when it gave one. */
export function refusalReason(error: unknown): string | null {
  if (!axios.isAxiosError(error)) return null
  const data: unknown = error.response?.data
  const message =
    typeof data === 'object' && data !== null ? (data as { message?: unknown }).message : null
  return typeof message === 'string' && message.trim() ? message.trim() : null
}

/** What the toast says when no answer came back: nothing is known to have failed. */
export const NOT_CONFIRMED = {
  exit: 'No answer came back, so this exit is not confirmed. Check Orders before sending it again.',
  exitAll:
    'No answer came back, so Exit all is not confirmed. Check Orders before sending it again.',
  cancel:
    'No answer came back, so this cancel is not confirmed. Check the order’s status before cancelling it again.',
  cancelAll:
    'No answer came back, so Cancel all is not confirmed. Check the orders’ status before cancelling again.',
} as const
