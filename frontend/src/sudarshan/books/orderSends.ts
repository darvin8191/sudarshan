/**
 * The calls the Positions and Orders pages make for one position or one order
 * (M6.9), in one place, so another screen that offers the same action makes the
 * very same call and says the very same things (the phone chart's line sheet,
 * M4.7 C6). Moved here from the pages unchanged: nothing in this file is a new
 * way to send an order.
 *
 * Each returns what became of it, so the caller can hold its button
 * (`books/sent.ts`). An answer that may have reached the broker is
 * "not confirmed", never "failed": a second exit would reverse the position.
 * `refresh` is the caller's own book read.
 */
import { tradingApi } from '@/api/trading'
import { units } from '@/sudarshan/books/cards/format'
import { spokenContract } from '@/sudarshan/watchlist/rows'
import type { Order, Position } from '@/types/trading'
import { showToast } from '@/utils/toast'
import {
  answerBody,
  answeredHeld,
  heldText,
  refusalReason,
  sentOrderIds,
  stillPlacing,
  thrownOutcome,
} from './sendOutcome'
import type { SendOutcome, SentAnswer } from './sent'

/** Exit one position at market: the Positions page's Close. */
export async function closePositionSend(
  position: Position,
  refresh: () => void
): Promise<SendOutcome | SentAnswer> {
  try {
    const response = await tradingApi.closePosition(
      position.symbol,
      position.exchange,
      position.product
    )
    if (response.status === 'success') {
      // SUDARSHAN: says what was sent, from the position, in the confirmation's words; the
      // server's sentence read "BUY Order Placed ... Qty: 0" for a long being sold.
      const held = Number(position.quantity) || 0
      const contract = spokenContract(position.symbol, position.exchange)
      showToast.success(
        held === 0
          ? `Exit sent: ${contract}`
          : `Exit sent: ${held > 0 ? 'sell' : 'buy'} ${units(Math.abs(held))} ${contract}`,
        'positions'
      )
      refresh()
      // The orders this placed are the exit's own, not any other on its side.
      return { outcome: 'sent', orderids: sentOrderIds(response) }
    }
    const held = answeredHeld(response)
    if (held) {
      showToast.warning(heldText('exit', held, response), 'positions')
      refresh()
      return held
    }
    showToast.error(response.message || 'Failed to close position', 'positions')
  } catch (err) {
    const outcome = thrownOutcome(err)
    if (outcome !== 'refused') {
      showToast.warning(heldText('exit', outcome, answerBody(err)), 'positions')
      refresh()
      return outcome
    }
    showToast.error(refusalReason(err) ?? 'Failed to close position', 'positions')
    // An earlier exit for it is still being placed (the app's 409): that one holds the row.
    if (stillPlacing(err)) return 'placing'
  }
  return 'refused'
}

/** How long after a cancel or a modify the book is read again, giving the broker a moment. */
const ORDER_REFRESH_MS = 1000

/** Cancel one working order: the Orders page's Cancel. */
export async function cancelOrderSend(orderid: string, refresh: () => void): Promise<SendOutcome> {
  try {
    const response = await tradingApi.cancelOrder(orderid)
    if (response.status === 'success') {
      showToast.success(`Order cancelled: ${orderid}`, 'orders')
      setTimeout(refresh, ORDER_REFRESH_MS)
      return 'sent'
    }
    const held = answeredHeld(response)
    if (held) {
      showToast.warning(heldText('cancel', held, response), 'orders')
      setTimeout(refresh, ORDER_REFRESH_MS)
      return held
    }
    showToast.error(response.message || 'Failed to cancel order', 'orders')
  } catch (error) {
    const outcome = thrownOutcome(error)
    if (outcome !== 'refused') {
      showToast.warning(heldText('cancel', outcome, answerBody(error)), 'orders')
      setTimeout(refresh, ORDER_REFRESH_MS)
      return outcome
    }
    showToast.error(refusalReason(error) ?? 'Failed to cancel order', 'orders')
  }
  return 'refused'
}

/** What a modify may change; the rest of the order goes back as it is. */
export interface ModifyForm {
  pricetype: string
  quantity: number
  price: number
  trigger_price: number
}

/**
 * Modify one working order: the Orders page's Modify Order. True once the
 * modification was accepted, which is when the caller closes its form.
 */
export async function modifyOrderSend(
  order: Order,
  form: ModifyForm,
  refresh: () => void
): Promise<boolean> {
  const pt = form.pricetype
  const sendsPrice = pt === 'LIMIT' || pt === 'SL'
  const sendsTrigger = pt === 'SL' || pt === 'SL-M'

  try {
    const response = await tradingApi.modifyOrder(order.orderid, {
      symbol: order.symbol,
      exchange: order.exchange,
      action: order.action,
      product: order.product,
      pricetype: pt,
      quantity: form.quantity,
      ...(sendsPrice && { price: form.price }),
      ...(sendsTrigger && { trigger_price: form.trigger_price }),
    })
    if (response.status === 'success') {
      showToast.success(`Order modified: ${order.orderid}`, 'orders')
      setTimeout(refresh, ORDER_REFRESH_MS)
      return true
    }
    showToast.error(response.message || 'Failed to modify order', 'orders')
  } catch (error) {
    const axiosError = error as { response?: { data?: { message?: string } } }
    const message = axiosError.response?.data?.message || 'Failed to modify order'
    showToast.error(message, 'orders')
  }
  return false
}
