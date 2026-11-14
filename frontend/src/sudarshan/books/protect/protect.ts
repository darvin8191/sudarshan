import type { GttOrder, Holding } from '@/types/trading'

/**
 * Protect (M5.6, G1): one sell GTT on a cash holding. Pure: the words, the checks, the request
 * and the reading of the GTT book, with nothing sent from here.
 *
 * It is this narrow on purpose. Upstox places a GTT's order itself when the trigger is reached,
 * and nothing on this server sees it fire. On a cash holding a sale with no shares behind it is
 * refused by Upstox, so it can never open a short; on an F&O position it would open one. So:
 * cash holdings only (NSE or BSE shares held for delivery), one Protect per holding, always a
 * SINGLE sell at one price, and never the two-leg kind, which on Upstox opens a position first.
 */

export type ProtectStage = 'off' | 'sandbox' | 'on'

/** `sandbox` shows Protect only in Analyze mode; `on` in Live too; anything else nowhere. */
export function protectShown(stage: unknown, sandbox: boolean): boolean {
  return stage === 'on' || (stage === 'sandbox' && sandbox)
}

/** The closest a trigger may sit to the last price, as a share of it. */
export const MIN_DISTANCE = 0.005
export const STRATEGY = 'Protect'
/** The start of the hold of a Protect being placed, which is kept by its holding. */
export const PLACING = 'protect:'
const CASH = new Set(['NSE', 'BSE'])

type Held = Pick<Holding, 'symbol' | 'exchange' | 'quantity' | 'product'>

/** Shares held for delivery on NSE or BSE: the only thing Protect is offered on. */
export function protectable(holding: Held): boolean {
  const quantity = Number(holding.quantity)
  return (
    CASH.has(String(holding.exchange).toUpperCase()) &&
    String(holding.product ?? 'CNC').toUpperCase() === 'CNC' &&
    Number.isInteger(quantity) &&
    quantity > 0
  )
}

export interface Protect {
  triggerId: string
  quantity: number
  trigger: number
}

/** The working sell GTTs on a holding, as the GTT book lists them. */
export function protectsOf(holding: Held, gtts: readonly GttOrder[]): Protect[] {
  const found: Protect[] = []
  for (const gtt of gtts) {
    if (String(gtt.status).toLowerCase() !== 'active') continue
    if (gtt.symbol !== holding.symbol || gtt.exchange !== holding.exchange) continue
    const leg = gtt.legs?.[0]
    if (!leg || String(leg.action).toUpperCase() !== 'SELL') continue
    const trigger = Number(gtt.trigger_prices?.[0])
    const quantity = Number(leg.quantity)
    if (!(trigger > 0) || !(quantity > 0)) continue
    found.push({ triggerId: String(gtt.trigger_id), quantity, trigger })
  }
  return found
}

const NUMBER = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const UNITS = new Intl.NumberFormat('en-IN')

/** The card's line: "Protected: sell 50 at 1,240.00". */
export function protectedLine(protect: Protect): string {
  return `Protected: sell ${UNITS.format(protect.quantity)} at ${NUMBER.format(protect.trigger)}`
}

export type TriggerCheck =
  | { ok: true; side: 'falls' | 'rises'; distance: number; percent: number }
  | { ok: false; sentence: string }

/**
 * Whether a trigger may be sent. The last price must be known: which way the price has to move
 * is read from it, and a trigger at the price would sell at once.
 */
export function checkTrigger(trigger: number | null, last: number | null): TriggerCheck {
  if (last === null || !(last > 0)) {
    return {
      ok: false,
      sentence:
        'The last price is not showing, so Protect cannot be set just now. Try again when prices show.',
    }
  }
  if (trigger === null || !Number.isFinite(trigger) || !(trigger > 0)) {
    return { ok: false, sentence: 'Type the price at which to sell.' }
  }
  const distance = trigger - last
  const percent = (distance / last) * 100
  if (Math.abs(distance) < last * MIN_DISTANCE) {
    return {
      ok: false,
      sentence: `That is within ${MIN_DISTANCE * 100}% of the last price, where it could sell at once. Choose a price further away, or sell from the card.`,
    }
  }
  return { ok: true, side: distance < 0 ? 'falls' : 'rises', distance, percent }
}

/** Whole shares, at least one and no more than are held. */
export function checkQuantity(quantity: number | null, held: number): string | null {
  if (quantity === null || !Number.isInteger(quantity) || quantity < 1) {
    return 'Type how many shares to sell, as a whole number.'
  }
  if (quantity > held) return `You hold ${UNITS.format(held)}. Protect cannot sell more than that.`
  return null
}

/** Why a new Protect cannot be set on this holding, or null when it can. */
export function refusal(holding: Held, existing: readonly Protect[]): string | null {
  if (!protectable(holding)) {
    return 'Protect is for shares you hold for delivery. It is not offered on futures and options.'
  }
  if (existing.length > 0) {
    return `${holding.symbol} already has a Protect order. Change it or remove it first.`
  }
  return null
}

export interface ProtectRequest {
  apikey: string
  strategy: string
  trigger_type: 'SINGLE'
  exchange: string
  symbol: string
  action: 'SELL'
  product: 'CNC'
  quantity: number
  pricetype: 'LIMIT'
  price: number
  triggerprice_sl: number
  triggerprice_tg: number
}

/**
 * The one request Protect sends: a SINGLE sell for delivery at one price. A price the market
 * must fall to goes as the stop's trigger, one it must rise to as the target's; the other is 0.
 * There is no other shape: never two legs, never a buy, never another product.
 */
export function protectRequest(
  apikey: string,
  holding: Pick<Holding, 'symbol' | 'exchange'>,
  quantity: number,
  trigger: number,
  side: 'falls' | 'rises'
): ProtectRequest {
  return {
    apikey,
    strategy: STRATEGY,
    trigger_type: 'SINGLE',
    exchange: holding.exchange,
    symbol: holding.symbol,
    action: 'SELL',
    product: 'CNC',
    quantity,
    pricetype: 'LIMIT',
    price: trigger,
    triggerprice_sl: side === 'falls' ? trigger : 0,
    triggerprice_tg: side === 'rises' ? trigger : 0,
  }
}

/** What the sheet says before anything is sent. */
export const LINES = [
  'Upstox places a limit order at your price when it is reached. In a fast fall it may not fill.',
  'It stays for up to one year, or until you remove it.',
  'Sell orders from a GTT need the one-time share authorisation in the Upstox app.',
] as const

export function asks(
  symbol: string,
  quantity: number,
  trigger: number,
  side: 'falls' | 'rises'
): string {
  return `Sell ${UNITS.format(quantity)} ${symbol} if the price ${side} to ${NUMBER.format(trigger)}?`
}

export function sellQuestion(protect: Protect): string {
  return `A Protect order for ${UNITS.format(protect.quantity)} shares is active. Remove it too?`
}

export const NOT_READ = 'Protect orders could not be read just now.'
