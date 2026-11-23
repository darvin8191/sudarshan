import { TriangleAlert } from 'lucide-react'
import { roundToTick, tickDecimals } from '@/components/trading/PlaceOrderDialog'

/**
 * The line a Repeat ticket shows before Place when the stop it copied has already been passed by
 * the last price (v0.17.0 server review L3, M6.1): Repeat copies a stop's trigger as it was, and
 * an owner placing a protective stop again may take it for one that rests. A buy stop triggers
 * at or above its trigger, a sell stop at or below it, so with the price already there it fills
 * at once (SL-M, or an SL whose limit the price is within); an SL whose limit the price is past
 * triggers and rests as a limit order. Words only: the ticket and what Place sends are unchanged.
 */

export interface StopFields {
  action: 'BUY' | 'SELL'
  priceType: string
  /** As typed; the order carries it on the tick, and so do these words. */
  trigger: number
  price: number
  ltp: number | undefined
  tickSize: number
}

/** The sentence, or null when the order is no stop, or its trigger has not been passed. */
export function passedTriggerText(order: StopFields): string | null {
  const { action, priceType, ltp, tickSize } = order
  if (priceType !== 'SL' && priceType !== 'SL-M') return null
  if (!(order.trigger > 0) || ltp === undefined || !(ltp > 0)) return null
  const trigger = roundToTick(order.trigger, tickSize)
  if (action === 'BUY' ? ltp < trigger : ltp > trigger) return null
  const said = (value: number) => roundToTick(value, tickSize).toFixed(tickDecimals(tickSize))
  const where = ltp === trigger ? 'at' : action === 'BUY' ? 'below' : 'above'
  const head = `The trigger ${said(trigger)} is already ${where} the last price ${said(ltp)}`
  if (priceType === 'SL-M') return `${head}: this stop would fill at once.`
  if (!(order.price > 0)) return `${head}: this stop would trigger at once.`
  const limit = roundToTick(order.price, tickSize)
  const fills = action === 'BUY' ? ltp <= limit : ltp >= limit
  return fills
    ? `${head}: this stop would fill at once.`
    : `${head}: this stop would trigger at once and rest as a limit order at ${said(limit)}.`
}

/** The line itself, in the warning ink with its sign; ``id`` describes the Place button. */
export function PassedTriggerLine({ id, text }: { id: string; text: string }) {
  return (
    <output
      id={id}
      data-testid="ticket-trigger-passed"
      className="flex items-start gap-1.5 text-[0.8125rem] leading-snug font-semibold text-[color:var(--warning-text)]"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{text}</span>
    </output>
  )
}
