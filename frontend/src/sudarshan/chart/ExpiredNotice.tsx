import { CalendarX2, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { spokenContract } from '@/sudarshan/watchlist/rows'
import { loadExpiries, loadInstrument } from './api'
import {
  type Contract,
  contractOf,
  dayWords,
  type Instrument,
  onExpiry,
  underlyingOf,
} from './expired'

/** Found, not listed, or not read. */
type Found = Instrument | 'none' | null

/** The same option (strike and side) or future on the nearest expiry, if the master lists it. */
async function nearest(apiKey: string, contract: Contract, exchange: string): Promise<Found> {
  const kind = contract.strike ? 'options' : 'futures'
  const expiries = await loadExpiries(apiKey, contract.base, exchange, kind)
  if (expiries === null) return null
  const symbol = expiries.length > 0 ? onExpiry(contract, expiries[0]) : null
  if (symbol === null) return 'none'
  const listed = await loadInstrument(apiKey, symbol, exchange)
  if (listed === null) return null
  return listed === 'unlisted' ? 'none' : { symbol, exchange }
}

/**
 * In place of the chart (TD2, M4.15): a contract that expired, or one the master does not list,
 * said with its day and never with Try again; what still trades is offered instead. Its two or
 * four reads go once, when the page opens.
 */
export function ExpiredNotice({
  symbol,
  exchange,
  endedOn,
  apiKey,
  onOpen,
}: {
  symbol: string
  exchange: string
  /** The day it expired, YYYY-MM-DD; null for one the master does not list. */
  endedOn: string | null
  /** Null until prices can be read: nothing is looked up before then. */
  apiKey: string | null
  onOpen(next: Instrument): void
}) {
  const contract = contractOf(symbol, exchange)
  const underlying = contract ? underlyingOf(contract, exchange) : null
  const [current, setCurrent] = useState<Found | undefined>(undefined)
  /** A commodity or currency option's underlying: the nearest future. */
  const [future, setFuture] = useState<Found | undefined>(undefined)

  useEffect(() => {
    const found = contractOf(symbol, exchange)
    if (!apiKey || !found) return
    let alive = true
    void nearest(apiKey, found, exchange).then((value) => alive && setCurrent(value))
    if (found.strike && !underlyingOf(found, exchange))
      void nearest(apiKey, { base: found.base, expiry: found.expiry }, exchange).then(
        (value) => alive && setFuture(value)
      )
    return () => {
      alive = false
    }
  }, [apiKey, symbol, exchange])

  const option = contract?.strike ? `${contract.strike} ${contract.right}` : null
  const offers: Array<Instrument & { caption: string }> = []
  if (current && typeof current === 'object')
    offers.push({
      ...current,
      caption: option ? 'Same strike and side, nearest expiry' : 'Nearest expiry',
    })
  if (underlying)
    offers.push({
      ...underlying,
      caption: underlying.exchange.endsWith('_INDEX') ? 'The index' : 'The share',
    })
  if (future && typeof future === 'object')
    offers.push({ ...future, caption: 'The underlying future, nearest expiry' })

  const said = {
    looking: option
      ? `Looking for ${option} on the nearest expiry.`
      : 'Looking for the nearest future.',
    none: option
      ? `${option} is not listed on the nearest expiry.`
      : 'No later future of it is listed.',
    failed: 'The nearest expiry could not be read just now.',
  }
  const state =
    current === undefined ? 'looking' : current === 'none' ? 'none' : current ? null : 'failed'
  const status = apiKey && contract && state ? said[state] : null

  return (
    <section
      aria-labelledby="chart-gone"
      data-testid="chart-gone"
      className="m-4 flex flex-col gap-3 rounded-2xl border bg-card p-4 text-sm shadow-[var(--shadow-card)]"
    >
      <h2 id="chart-gone" className="flex items-start gap-2 text-base font-semibold">
        <CalendarX2
          aria-hidden="true"
          className="mt-0.5 size-5 shrink-0 text-[color:var(--warning-text)]"
        />
        {endedOn ? `Expired on ${dayWords(endedOn)}` : "Not in today's contract list"}
      </h2>
      <p className="text-muted-foreground">
        {endedOn
          ? `${symbol} expired on ${dayWords(endedOn)}. An expired contract has no prices and takes no orders, so there is nothing to chart.`
          : `Today's contract list does not have ${symbol}, so it cannot be charted or traded. Check its expiry and strike.`}
      </p>
      {offers.length > 0 && (
        <ul aria-label="Open instead" className="flex flex-col gap-2">
          {offers.map((offer) => (
            <li key={`${offer.exchange}:${offer.symbol}`}>
              <button
                type="button"
                aria-label={`${spokenContract(offer.symbol, offer.exchange)}, ${offer.caption}`}
                onClick={() => onOpen({ symbol: offer.symbol, exchange: offer.exchange })}
                className="flex min-h-12 w-full items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="font-semibold [overflow-wrap:anywhere]">
                    {spokenContract(offer.symbol, offer.exchange)}
                  </span>
                  <span className="text-xs font-medium text-muted-foreground">{offer.caption}</span>
                </span>
                <ChevronRight aria-hidden="true" className="size-4 shrink-0" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {status && <p className="text-xs font-medium text-muted-foreground">{status}</p>}
    </section>
  )
}
