import { ChevronDown, ReceiptIndianRupee } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { formatRupees } from '@/sudarshan/dashboard/format'
import { journalCall } from '@/sudarshan/journal/api'
import { paiseAmount } from '@/sudarshan/journal/money'
import { type ChargeParts, type OrderCharges, PRICE_SCALE } from '@/sudarshan/journal/types'

/**
 * The order ticket's charges line (B6: M5.10): "Charges est. ₹x", the journal's estimate of the
 * order as it stands in the ticket (`charges_estimate`, the rate card of
 * `sudarshan/journal/core/charges.py`). Read when the ticket opens and again 400 ms after the
 * last change of side, quantity, price, type or product, one read at a time, as the margin line
 * is. A market order is priced at the last price when it is read, so a moving price asks
 * nothing again.
 *
 * What could not be read says "not read", never ₹0: the journal switched off, its service not
 * answering, or an op it does not have yet. An exchange the card does not cover says so in
 * words. A tap shows the parts. The line only informs: it changes nothing the ticket sends.
 */

/** How long after the last change the estimate is read again: the margin line's wait. */
export const CHARGES_SETTLE_MS = 400

export interface ChargesOrder {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
  pricetype: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
  product: string
  price: number
  triggerPrice: number
}

/**
 * The price the order would trade at, as the ticket holds it: a limit or stop-limit's own
 * price, a stop-market's trigger, a market order's last price; null while there is none.
 */
export function tradePrice(order: ChargesOrder, ltp: number | undefined): number | null {
  const value =
    order.pricetype === 'LIMIT' || order.pricetype === 'SL'
      ? order.price
      : order.pricetype === 'SL-M'
        ? order.triggerPrice
        : (ltp ?? 0)
  return Number.isFinite(value) && value > 0 ? value : null
}

/**
 * What the estimate is read again for: the instrument, side, quantity, type, product and the
 * price the ticket holds. A market order counts only whether a last price is known. Null when
 * the ticket holds nothing that can be priced yet.
 */
export function estimateKey(order: ChargesOrder, ltp: number | undefined): string | null {
  if (!order.symbol || !order.exchange) return null
  if (!Number.isInteger(order.quantity) || order.quantity <= 0) return null
  const price = tradePrice(order, ltp)
  if (price === null) return null
  return JSON.stringify([
    order.symbol,
    order.exchange,
    order.action,
    order.quantity,
    order.pricetype,
    order.product,
    order.pricetype === 'MARKET' ? 'last' : price,
  ])
}

/** The estimate for the order at `price`, or null when it could not be read. */
export async function readCharges(
  order: ChargesOrder,
  price: number
): Promise<OrderCharges | null> {
  try {
    const result = await journalCall('charges_estimate', {
      exchange: order.exchange,
      symbol: order.symbol,
      action: order.action,
      quantity: order.quantity,
      price_u: Math.round(price * PRICE_SCALE),
      product: order.product,
    })
    return result.enabled ? result.data : null
  } catch {
    return null
  }
}

const PARTS: ReadonlyArray<[keyof ChargeParts, string]> = [
  ['brokerage_paise', 'Brokerage'],
  ['stt_paise', 'STT'],
  ['exchange_paise', 'Exchange charges'],
  ['sebi_paise', 'SEBI fee'],
  ['stamp_paise', 'Stamp duty'],
  ['gst_paise', 'GST'],
]
const PLAN_WORDS: Record<string, string> = {
  plus: 'Upstox Plus plan',
  standard: 'Upstox standard plan',
}
export const NOT_ESTIMATED = 'not estimated for this exchange'

/** One read of the estimate, for the order as it stood when asked, and the price it used. */
interface ChargesRead {
  key: string
  value: OrderCharges | null
  price: number
}

export function ChargesLine({ order, ltp }: { order: ChargesOrder; ltp: number | undefined }) {
  const key = estimateKey(order, ltp)
  const [read, setRead] = useState<ChargesRead | null>(null)
  const [open, setOpen] = useState(false)
  const partsId = useId()
  const partsBox = useRef<HTMLDivElement>(null)

  // The order as it stands, for the read that runs after the wait.
  const latest = useRef({ order, ltp, key })
  useLayoutEffect(() => {
    latest.current = { order, ltp, key }
  })
  const alive = useRef(true)
  const reading = useRef(false)
  const again = useRef(false)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // The line sits at the foot of the ticket's scrolling body: opened parts are brought into view.
  useEffect(() => {
    if (open) partsBox.current?.scrollIntoView?.({ block: 'nearest' })
  }, [open])

  useEffect(() => {
    if (key === null) return
    const timer = setTimeout(async () => {
      if (reading.current) {
        again.current = true
        return
      }
      reading.current = true
      try {
        do {
          again.current = false
          const asked = latest.current
          const price = asked.key === null ? null : tradePrice(asked.order, asked.ltp)
          if (asked.key === null || price === null) break
          const value = await readCharges(asked.order, price)
          if (!alive.current) return
          setRead({ key: asked.key, value, price })
        } while (again.current)
      } finally {
        reading.current = false
      }
    }, CHARGES_SETTLE_MS)
    return () => clearTimeout(timer)
  }, [key])

  const fresh = read !== null && key !== null && read.key === key
  // A figure already shown stays while the next read is waited for, marked "updating".
  const kept = key !== null && read?.value?.modelled === true
  const shown = fresh || kept ? read : null
  let figure: string
  if (key === null) figure = '-'
  else if (shown === null) figure = 'reading'
  else if (shown.value === null) figure = 'not read'
  else if (!shown.value.modelled) figure = NOT_ESTIMATED
  else figure = paiseAmount(shown.value.parts.total_paise)
  const updating = shown !== null && !fresh
  const parts = shown?.value?.modelled ? shown.value : null

  const words = (
    <span className="min-w-0">
      <span className="text-[color:var(--text-secondary)]">Charges est.</span>{' '}
      <span className="font-semibold">{figure}</span>
      {updating && (
        <>
          {' '}
          <span className="text-[color:var(--text-tertiary)]">updating</span>
        </>
      )}
    </span>
  )
  return (
    <div data-testid="charges-line" className="rounded-lg bg-[color:var(--surface-2)] text-sm">
      {parts ? (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={partsId}
          onClick={() => setOpen((was) => !was)}
          className="flex min-h-12 w-full items-center gap-2 rounded-lg px-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ReceiptIndianRupee
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground"
          />
          {words}{' '}
          <span className="ml-auto inline-flex shrink-0 items-center gap-1 text-xs font-semibold text-[color:var(--brand)]">
            {open ? 'Hide parts' : 'Show parts'}
            <ChevronDown aria-hidden="true" className={open ? 'size-4 rotate-180' : 'size-4'} />
          </span>
        </button>
      ) : (
        <p className="flex items-start gap-2 px-3 py-2">
          <ReceiptIndianRupee
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          />
          {words}
        </p>
      )}
      {parts && open && shown && (
        <div id={partsId} ref={partsBox} className="flex flex-col gap-1 px-3 pb-3">
          <ul aria-label="Estimated charges, part by part" className="flex flex-col gap-1">
            {PARTS.map(([name, label]) => (
              <li key={name} className="flex items-baseline justify-between gap-3">
                <span className="text-[color:var(--text-secondary)]">{label}</span>
                <span className="font-semibold">{paiseAmount(parts.parts[name])}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-[color:var(--text-tertiary)]">
            An estimate from the journal’s rate card for one executed order at{' '}
            {formatRupees(shown.price)} a unit, on the {PLAN_WORDS[parts.plan] ?? parts.plan}.
            Upstox’s contract note has the actual charges.
          </p>
          {parts.left_out.length > 0 && (
            <p className="text-xs text-[color:var(--text-tertiary)]">
              Not in this estimate: {parts.left_out.join(', ')}.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
