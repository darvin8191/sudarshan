import axios from 'axios'

/**
 * What the answer to an exit or a cancel says about the order (M6.9). Anything
 * that may have reached the broker is "not confirmed", never "failed": a
 * trader told it failed sends it again, and a second exit sized from a book
 * that has not caught up reverses the position instead of closing it.
 *
 * - No answer (the network dropped, the 45 s wait ran out), and every 5xx,
 *   whoever sent it: the app's own JSON 500 is what a read timeout or a
 *   dropped connection after Upstox took the order turns into, and the route
 *   answers 502 when Upstox took it without naming an order.
 * - An answer the app marks `not_confirmed`, whatever its code: a close-all
 *   that sent some exits before the order window refused the rest.
 * - Refused: any other 4xx, which the app gives only before anything was
 *   sent (a missing field, the login or the security token, no position to
 *   exit, the rate limit), and a request that never left.
 */

/** Whether a body is the app's own answer: a JSON object with its status word. */
export function appAnswered(body: unknown): body is { status: string; message?: unknown } {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as { status?: unknown }).status === 'string'
  )
}

/** Whether the app says this answer may have reached the broker. */
export function notConfirmed(body: unknown): boolean {
  return appAnswered(body) && (body as { not_confirmed?: unknown }).not_confirmed === true
}

/**
 * What a send that threw says: `refused` when the app answered no before
 * anything was sent, or when the request never left (the security token step
 * before it failed); `unconfirmed` otherwise.
 */
export function thrownOutcome(error: unknown): 'refused' | 'unconfirmed' {
  if (!axios.isAxiosError(error)) return 'refused'
  const response = error.response
  if (!response) return 'unconfirmed'
  if (response.status >= 500) return 'unconfirmed'
  if (notConfirmed(response.data)) return 'unconfirmed'
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

/**
 * What the toast says when it is not confirmed, whatever the cause: nothing is
 * known to have failed, and the app's own words, never an exception's.
 */
export const NOT_CONFIRMED = {
  exit: 'This exit is not confirmed: it may have gone through. Check Orders before sending it again.',
  exitAll:
    'Exit all is not confirmed: some exits may have gone through. Check Orders before sending it again.',
  cancel:
    'This cancel is not confirmed: it may have gone through. Check the order’s status before cancelling it again.',
  cancelAll:
    'Cancel all is not confirmed: some cancels may have gone through. Check the orders’ status before cancelling again.',
} as const
