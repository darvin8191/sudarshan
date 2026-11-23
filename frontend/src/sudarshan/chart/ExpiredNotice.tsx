import { CalendarX2, ChevronRight, Hourglass } from 'lucide-react'
import { useEffect, useState } from 'react'
import { expiredWords, spokenContract } from '@/sudarshan/watchlist/rows'
import { loadExpiries, loadInstrument } from './api'
import { type Contract, contractOf, type Instrument, onExpiry, underlyingOf } from './expired'

/** Found, not listed, or not read. */
type Found = Instrument | 'none' | null

/** The same option (strike and side) or future on the nearest expiry, if the master lists it. */
async function nearest(apiKey: string, contract: Contract, exchange: string): Promise<Found> {
  const kind = contract.strike ? 'options' : 'futures'
  const expiries = await loadExpiries(apiKey, contract.base, exchange, kind)
  if (expiries === null) return null
  const symbol = expiries.length > 0 ? onExpiry(contract, expiries[0], exchange) : null
  if (symbol === null) return 'none'
  const listed = await loadInstrument(apiKey, symbol, exchange)
  if (listed === null) return null
  return listed === 'unlisted' ? 'none' : { symbol, exchange }
}

/** M2: how often the page reads a contract again while no expiry of its underlying comes back, and how many times. */
export const LIST_RETRY_MS = 30_000
export const LIST_TRIES = 20

/**
 * v0.17.0 phone review M2: whether the master lists the expiries of a contract's underlying (of
 * its kind). The master answers 404 for every contract while a login loads the contract list
 * again, so a 404 means "not listed" only when this comes back with dates; a symbol that is not a
 * dated contract has nothing to wait for.
 */
export async function expiriesListed(
  apiKey: string,
  symbol: string,
  exchange: string
): Promise<boolean> {
  const contract = contractOf(symbol, exchange)
  if (!contract) return true
  const kind = contract.strike ? 'options' : 'futures'
  const expiries = await loadExpiries(apiKey, contract.base, exchange, kind)
  return expiries !== null && expiries.length > 0
}

/**
 * In place of the chart while the master answers 404 and no expiry of the underlying comes back
 * (M2): never "Expired", never Try again; the page reads it again on its own (`LIST_RETRY_MS`).
 */
export function ListLoadingNotice({ symbol, exchange }: { symbol: string; exchange: string }) {
  const base = contractOf(symbol, exchange)?.base ?? symbol
  return (
    <section
      aria-labelledby="chart-list"
      data-testid="chart-list-loading"
      className="m-4 flex flex-col gap-3 rounded-2xl border bg-card p-4 text-sm shadow-[var(--shadow-card)]"
    >
      <h2 id="chart-list" className="flex items-start gap-2 text-base font-semibold">
        <Hourglass
          aria-hidden="true"
          className="mt-0.5 size-5 shrink-0 text-[color:var(--warning-text)]"
        />
        Today's contract list is not ready
      </h2>
      <p className="text-muted-foreground [overflow-wrap:anywhere]">
        {`The server does not list ${symbol} just now, and no ${base} expiry came back either: that is how the contract list looks while a login loads it again, which takes a minute or two. This page checks every 30 seconds and opens the chart once it is listed.`}
      </p>
    </section>
  )
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
        {endedOn ? expiredWords(endedOn) : "Not in today's contract list"}
      </h2>
      <p className="text-muted-foreground">
        {endedOn
          ? `${symbol} has expired. An expired contract has no prices and takes no orders, so there is nothing to chart.`
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
