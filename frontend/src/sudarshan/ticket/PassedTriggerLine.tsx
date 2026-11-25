import { TriangleAlert } from 'lucide-react'
import { roundToTick, tickDecimals } from '@/components/trading/PlaceOrderDialog'

/**
 * The line a Repeat ticket shows before Place when the stop it copied has already been passed by
 * the last price (v0.17.0 server review L3, M6.1): Repeat copies a stop's trigger as it was, and
 * an owner placing a protective stop again may take it for one that rests. A buy stop triggers
 * at or above its trigger, a sell stop at or below it. What then happens depends on where the
 * order goes (v0.18.0 verification V3). In Analyze mode it is the sandbox's own rule
 * (`sandbox/order_manager.py`): it fills at once (SL-M, or an SL whose limit the price is
 * within), and an SL whose limit the price is past triggers and rests as a limit order. In Live
 * the line does not guess what Upstox does: it may refuse such a stop or take it at once, and
 * Market or Limit is how to close now. Words only: the ticket and what Place sends are unchanged.
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

/**
 * The sentence, or null when the order is no stop, or its trigger has not been passed. ``sandbox``:
 * the order goes to the sandbox (Analyze mode); otherwise to Upstox (Live).
 */
export function passedTriggerText(order: StopFields, sandbox: boolean): string | null {
  const { action, priceType, ltp, tickSize } = order
  if (priceType !== 'SL' && priceType !== 'SL-M') return null
  if (!(order.trigger > 0) || ltp === undefined || !(ltp > 0)) return null
  const trigger = roundToTick(order.trigger, tickSize)
  if (action === 'BUY' ? ltp < trigger : ltp > trigger) return null
  const said = (value: number) => roundToTick(value, tickSize).toFixed(tickDecimals(tickSize))
  const head =
    ltp === trigger
      ? `The last price ${said(ltp)} is already at the trigger ${said(trigger)}`
      : `The last price ${said(ltp)} has already passed the trigger ${said(trigger)}`
  const limit = priceType === 'SL' && order.price > 0 ? roundToTick(order.price, tickSize) : null
  const fills =
    priceType === 'SL-M' || (limit !== null && (action === 'BUY' ? ltp <= limit : ltp >= limit))
  if (sandbox) {
    if (fills) return `${head}: in the sandbox this stop would fill at once.`
    if (limit === null) return `${head}: in the sandbox this stop would trigger at once.`
    return `${head}: in the sandbox this stop would trigger at once and rest as a limit order at ${said(limit)}.`
  }
  const taken = fills
    ? 'fill it at once'
    : limit === null
      ? 'trigger it at once'
      : `trigger it at once and rest it as a limit order at ${said(limit)}`
  return `${head}: Upstox may refuse such a stop or ${taken}. To close a position now, use Market or Limit.`
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
