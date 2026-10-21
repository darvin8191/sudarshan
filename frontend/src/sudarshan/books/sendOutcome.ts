import axios from 'axios'

/**
 * What the answer to an exit or a cancel says about the order (M6.9). Anything
 * that may have reached the broker is "not confirmed", never "failed": a
 * trader told it failed sends it again, and a second exit sized from a book
 * that has not caught up reverses the position instead of closing it.
 *
 * - No answer from the app at all (the network dropped, the 45 s wait ran
 *   out, or something in between answered for it): `unanswered`. The server
 *   may still be placing the order (one Upstox call can take 120 s), so the
 *   hold is longer (books/sent.ts).
 * - The app's own 5xx, and any answer it marks `not_confirmed` whatever its
 *   code: `unconfirmed`. Its view has ended, and the order may have gone: a
 *   read timeout or a dropped connection after Upstox took it is the app's
 *   JSON 500; Upstox taking it without naming an order is its 502; a close-all
 *   that sent some exits before the rest were refused is marked.
 * - Refused: any other 4xx, which the app gives only before anything was
 *   sent (a missing field, the login or the security token, no position to
 *   exit, an exit for it still being placed, the rate limit), and a request
 *   that never left.
 */

/** A send that is not a refusal: the app could not confirm it, or no answer came at all. */
export type HeldOutcome = 'unconfirmed' | 'unanswered'

/** The four sends the books make, for the words their toasts use. */
export type SendKind = 'exit' | 'exitAll' | 'cancel' | 'cancelAll'

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
 * before it failed); `unconfirmed` when the app answered and could not
 * confirm it; `unanswered` when no answer of the app's own came back.
 */
export function thrownOutcome(error: unknown): 'refused' | HeldOutcome {
  if (!axios.isAxiosError(error)) return 'refused'
  const response = error.response
  if (!response) return 'unanswered'
  if (response.status >= 500) return appAnswered(response.data) ? 'unconfirmed' : 'unanswered'
  if (notConfirmed(response.data)) return 'unconfirmed'
  return 'refused'
}

/**
 * What a 2xx says when it is not the success it should be: `unanswered` when
 * it is not the app's own answer (a page from something in between),
 * `unconfirmed` when the app marks it, null when it is the app saying no.
 */
export function answeredHeld(body: unknown): HeldOutcome | null {
  if (!appAnswered(body)) return 'unanswered'
  return notConfirmed(body) ? 'unconfirmed' : null
}

/** The body of the answer a send threw with, if one came. */
export function answerBody(error: unknown): unknown {
  return axios.isAxiosError(error) ? error.response?.data : undefined
}

/** The app's own reason for a refusal, when it gave one. */
export function refusalReason(error: unknown): string | null {
  if (!axios.isAxiosError(error)) return null
  const data: unknown = error.response?.data
  const message =
    typeof data === 'object' && data !== null ? (data as { message?: unknown }).message : null
  return typeof message === 'string' && message.trim() ? message.trim() : null
}

/** The order an exit's answer says it placed, when it names one. */
export function sentOrderId(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const id = (body as { orderid?: unknown }).orderid
  if (typeof id === 'number') return String(id)
  return typeof id === 'string' && id.trim() ? id.trim() : null
}

/** What the toast says when it is not confirmed and the app said nothing of its own. */
export const NOT_CONFIRMED = {
  exit: 'This exit is not confirmed: it may have gone through. Check Orders before sending it again.',
  exitAll:
    'Exit all is not confirmed: some exits may have gone through. Check Orders before sending it again.',
  cancel:
    'This cancel is not confirmed: it may have gone through. Check the order’s status before cancelling it again.',
  cancelAll:
    'Cancel all is not confirmed: some cancels may have gone through. Check the orders’ status before cancelling again.',
} as const

/**
 * What the toast says when no answer came: the server may still be at it, and
 * an order still being placed does not show in Orders yet.
 */
export const NO_ANSWER = {
  exit: 'No answer came for this exit: it may still be going through. Wait for Positions or Orders to show it before sending another.',
  exitAll:
    'No answer came for Exit all: its exits may still be going through. Wait for Positions or Orders to show them before sending again.',
  cancel:
    'No answer came for this cancel: it may still be going through. Wait for the order’s status to change before cancelling again.',
  cancelAll:
    'No answer came for Cancel all: its cancels may still be going through. Wait for the orders’ status to change before cancelling again.',
} as const

/**
 * The toast for a send that is not a refusal. Without an answer, ours. With
 * one the app marks not confirmed, the app's own sentence (it writes them for
 * a trader, and says how many of an Exit all went); with any other, ours,
 * never an exception's words.
 */
export function heldText(kind: SendKind, outcome: HeldOutcome, body?: unknown): string {
  if (outcome === 'unanswered') return NO_ANSWER[kind]
  if (notConfirmed(body)) {
    const message = (body as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
  }
  return NOT_CONFIRMED[kind]
}

/**
 * The one toast an Exit all's result shows: the page's answer and the
 * close_position_event the server sends for the same result share it.
 */
export const EXIT_ALL_TOAST = 'sudarshan-exit-all-result'
