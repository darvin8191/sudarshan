import { apiClient } from '@/api/client'
import { SEND_TIMEOUT_MS, tradingApi } from '@/api/trading'
import { useAuthStore } from '@/stores/authStore'
import {
  answerBody,
  answeredHeld,
  type HeldOutcome,
  notConfirmed,
  refusalReason,
  thrownOutcome,
  withoutCode,
} from '@/sudarshan/books/sendOutcome'
import type { SendOutcome } from '@/sudarshan/books/sent'
import type { GttOrder, Holding } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { protectRequest, protectsOf, refusal } from './protect'

/**
 * Protect's three sends (M5.6, G2). ORDER PATH, shown only where SUDARSHAN_GTT_PROTECT says.
 *
 * No order code of its own: a new Protect goes to OpenAlgo's GTT service (`/api/v1/placegttorder`),
 * so it passes the risk gate like any GTT, and Change and Remove are the GTT tab's own calls.
 * What is sent is always `protectRequest`: a SINGLE sell for delivery. Anything that may have
 * reached Upstox reads "not confirmed", never a refusal.
 */

type Held = Pick<Holding, 'symbol' | 'exchange' | 'quantity' | 'product'>

const WORDS = {
  set: {
    unconfirmed:
      'This Protect is not confirmed: it may have been set. Check the GTT tab in Orders before setting it again.',
    unanswered:
      'No answer came for this Protect: it may still be going through. Wait for the card to say Protected before setting it again.',
    refused: 'Protect was not set.',
  },
  change: {
    unconfirmed:
      'This change is not confirmed: it may have gone through. Check the price on the card before changing it again.',
    unanswered:
      'No answer came for this change: it may still be going through. Check the price on the card before changing it again.',
    refused: 'Protect was not changed.',
  },
  remove: {
    unconfirmed:
      'This removal is not confirmed: it may have gone through. Check the card before removing it again.',
    unanswered:
      'No answer came for this removal: it may still be going through. Wait for the card to stop saying Protected before removing it again.',
    refused: 'Protect was not removed.',
  },
} as const

function heldWords(kind: keyof typeof WORDS, outcome: HeldOutcome, body: unknown): string {
  if (outcome === 'unconfirmed' && notConfirmed(body)) {
    const message = (body as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
  }
  return WORDS[kind][outcome]
}

/** One send, read as the books read theirs: sent, held as not confirmed, or refused in words. */
async function run(
  kind: keyof typeof WORDS,
  done: string,
  call: () => Promise<{ status: string; message?: string }>
): Promise<SendOutcome> {
  try {
    const body = await call()
    if (body.status === 'success') {
      showToast.success(done, 'orders')
      return 'sent'
    }
    const held = answeredHeld(body)
    if (held) {
      showToast.warning(heldWords(kind, held, body), 'orders')
      return held
    }
    showToast.error(`${WORDS[kind].refused} ${withoutCode(body.message)}`.trim(), 'orders')
  } catch (error) {
    const outcome = thrownOutcome(error)
    if (outcome !== 'refused') {
      showToast.warning(heldWords(kind, outcome, answerBody(error)), 'orders')
      return outcome
    }
    showToast.error(`${WORDS[kind].refused} ${withoutCode(refusalReason(error))}`.trim(), 'orders')
  }
  return 'refused'
}

/**
 * A new Protect. The GTT book is read again first: one Protect per holding, whatever another
 * screen or the Upstox app did since this sheet opened. When it cannot be read, nothing is sent.
 */
export async function placeProtect(
  holding: Held,
  quantity: number,
  trigger: number,
  side: 'falls' | 'rises',
  _book?: unknown
): Promise<SendOutcome> {
  const apiKey = useAuthStore.getState().apiKey
  if (!apiKey) {
    showToast.error('Protect was not set. Log in again, then try once more.', 'orders')
    return 'refused'
  }
  let gtts: GttOrder[]
  try {
    const answer = await tradingApi.getGttOrderbook(apiKey, 'active')
    if (answer.status !== 'success') throw new Error('not read')
    gtts = (answer.data as GttOrder[]) ?? []
  } catch {
    showToast.error(
      'Protect was not set: the GTT book could not be read to check for one already there. Try again in a moment.',
      'orders'
    )
    return 'refused'
  }
  const why = refusal(holding, protectsOf(holding, gtts))
  if (why) {
    showToast.error(`Protect was not set. ${why}`, 'orders')
    return 'refused'
  }
  return run('set', `Protect set on ${holding.symbol}.`, async () => {
    const response = await apiClient.post(
      '/placegttorder',
      protectRequest(apiKey, holding, quantity, trigger, side),
      { timeout: SEND_TIMEOUT_MS }
    )
    return response.data
  })
}

/** Change a Protect's price or quantity: the GTT tab's own modify call, the same single sell. */
export function changeProtect(
  triggerId: string,
  holding: Held,
  quantity: number,
  trigger: number,
  side: 'falls' | 'rises'
): Promise<SendOutcome> {
  const { apikey: _apikey, ...body } = protectRequest('', holding, quantity, trigger, side)
  return run('change', `Protect changed on ${holding.symbol}.`, () =>
    tradingApi.modifyGttOrder(triggerId, body)
  )
}

/** Remove a Protect: the GTT tab's own cancel call. */
export function removeProtect(triggerId: string): Promise<SendOutcome> {
  return run('remove', 'Protect removed.', () => tradingApi.cancelGttOrder(triggerId))
}
