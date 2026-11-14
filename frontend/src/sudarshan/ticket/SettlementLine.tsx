import { PackageCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { instrumentSessionFor } from '@/lib/trading/instrumentSession'
import { cn } from '@/lib/utils'

/**
 * The delivery-settlement line (B7: M5.21), in the order ticket and on a position's card: a
 * stock's option or future held to its expiry is settled by delivery of the shares, which needs
 * the full value of the shares (or the shares themselves), not just the margin. Shown for such a
 * contract in its expiry week. An index contract is settled in cash and never shows it.
 *
 * Whether a contract is a stock's, and its expiry, are the server's facts (the master's, asked
 * once per contract per day); the phone works out neither. Without them nothing is shown.
 */

const DAY = new Intl.DateTimeFormat('en-IN', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  timeZone: 'UTC',
})

/** "Thu 26 Nov" for 2026-11-26. */
export function settlementDay(iso: string): string {
  const parts = DAY.formatToParts(new Date(`${iso}T00:00:00Z`))
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? ''
  return `${pick('weekday')} ${pick('day')} ${pick('month')}`
}

export function settlementText(iso: string): string {
  return `Stock contract: settled by delivery if held to expiry on ${settlementDay(iso)}.`
}

const DERIVATIVES = new Set(['NFO', 'BFO'])

/** The expiry a stock contract in its expiry week is delivered on, or null. */
export function useDeliveryExpiry(symbol: string, exchange: string): string | null {
  const [expiry, setExpiry] = useState<string | null>(null)
  useEffect(() => {
    setExpiry(null)
    if (!symbol || !DERIVATIVES.has(exchange.toUpperCase())) return
    let live = true
    void instrumentSessionFor(symbol, exchange.toUpperCase()).then((facts) => {
      if (live) setExpiry(facts?.delivery ?? null)
    })
    return () => {
      live = false
    }
  }, [symbol, exchange])
  return expiry
}

export function SettlementLine({
  symbol,
  exchange,
  className,
}: {
  symbol: string
  exchange: string
  className?: string
}) {
  const expiry = useDeliveryExpiry(symbol, exchange)
  if (!expiry) return null
  return (
    <span
      data-settlement="delivery"
      className={cn(
        'flex items-start gap-1.5 text-[0.8125rem] leading-snug font-semibold text-[color:var(--warning-text)]',
        className
      )}
    >
      <PackageCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>{settlementText(expiry)}</span>
    </span>
  )
}
