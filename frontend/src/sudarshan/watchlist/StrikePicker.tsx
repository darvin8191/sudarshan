import { Check, Layers, Loader2, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { watchlistError } from '@/api/watchlist'
import type { SymbolData } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { priceOf } from '@/sudarshan/dashboard/live'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { showToast } from '@/utils/toast'
import { BottomSheet } from './BottomSheet'
import { type FollowForm, num } from './followRows'
import { priceDecimals, statusLine } from './rows'
import { chainPath } from './SymbolSheet'
import { NO_RULE, ruleFor } from './strikePicker'
import type { LiveRow } from './useLiveRows'
import type { Ladder } from './useStrikeLadder'
import type { Contract, Strike } from './v2/types'

const TAP =
  'inline-flex min-h-12 items-center justify-center gap-1.5 rounded-[0.875rem] px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60'
const OUTLINED = cn(TAP, 'border border-input bg-card text-foreground active:bg-accent')

export interface StrikePickerProps {
  underlying: string
  /** The underlying's own exchange (NSE_INDEX, NSE, BSE_INDEX or BSE). */
  exchange: string
  ladder: Ladder
  /** The underlying's row, for its price and the feed line. */
  under: LiveRow | null
  lines: ReadonlyMap<string, SymbolData>
  feed: FeedState
  now: number
  marketOpen: boolean | null
  /** EXCHANGE:SYMBOL of everything the list holds. */
  held: ReadonlySet<string>
  full: boolean
  listName: string
  onAdd: (symbol: string, exchange: string) => Promise<void>
  onFollow: (form: FollowForm) => void
  /** The page's chain link for the underlying, or null. */
  chain: { exchange: string; underlying: string } | null
  onClose: () => void
}

/** A leg: its price, ticking, and Add CE or Add PE, or Added. */
function Leg({
  side,
  strike,
  contract,
  price,
  decimals,
  added,
  busy,
  blocked,
  onAdd,
}: {
  side: 'CE' | 'PE'
  strike: number
  contract: Contract | null
  price: number | null
  decimals: number
  added: boolean
  busy: boolean
  blocked: boolean
  onAdd: () => void
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <p
        data-leg-price={side}
        className="flex items-baseline justify-between gap-2 text-sm font-semibold"
      >
        <span className="text-xs font-bold text-[color:var(--text-tertiary)]">{side}</span>
        {contract === null ? (
          <span>
            <span aria-hidden="true">&mdash;</span>
            <span className="sr-only">no {side} contract</span>
          </span>
        ) : price === null ? (
          <span>
            <span aria-hidden="true">&mdash;</span>
            <span className="sr-only">no price yet</span>
          </span>
        ) : (
          <span>{formatPrice(price, decimals)}</span>
        )}
      </p>
      <button
        type="button"
        disabled={contract === null || added || blocked || busy}
        onClick={onAdd}
        aria-label={
          added ? `${side} at ${num(strike)} is in the list` : `Add ${side} at ${num(strike)}`
        }
        className={cn(OUTLINED, added && 'border-0 bg-[color:var(--primary-tint)] text-primary')}
      >
        {busy ? (
          <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        ) : added ? (
          <Check aria-hidden="true" className="size-4" />
        ) : (
          <Plus aria-hidden="true" className="size-4" />
        )}
        {added ? 'Added' : `Add ${side}`}
      </button>
    </div>
  )
}

/**
 * The strike picker (M8.18, F14): the underlying's price with its feed line, the expiry tabs as the
 * server labels them, and the strikes ATM plus or minus ten, the ATM marked by the word "ATM". Each
 * strike's CE and PE tick while it is open (the page follows them, counted in the cap, and lets them go
 * when it closes or the tab changes); Add CE and Add PE add that contract to the list, Add as one row opens
 * F11/F12's form on that strike and the tab's expiry.
 */
export function StrikePicker(props: StrikePickerProps) {
  const { ladder, under, lines, held, full } = props
  const [busy, setBusy] = useState<string | null>(null)
  const [done, setDone] = useState<ReadonlySet<string>>(() => new Set())
  const data = ladder.data
  const rule = ladder.tab ? ruleFor(ladder.expiries, ladder.tab) : null

  const line = statusLine({
    now: props.now,
    marketOpen: props.marketOpen,
    lastTickAt: under?.tickAt ?? null,
    lastUpdateAt: under?.view.at ?? null,
    connected: props.feed.connected,
    fallback: props.feed.fallback,
  })
  const ltp = under?.view.ltp ?? null
  const add = async (contract: Contract) => {
    setBusy(contract.key)
    try {
      await props.onAdd(contract.symbol, contract.exchange)
      setDone((was) => new Set([...was, contract.key]))
    } catch (error) {
      showToast.error(watchlistError(error, `Could not add ${contract.symbol}`))
    } finally {
      setBusy(null)
    }
  }
  const stands = (contract: Contract | null) =>
    contract !== null && (held.has(contract.key) || done.has(contract.key))
  const priceAt = (contract: Contract | null) => {
    const tick = contract ? priceOf(lines.get(contract.key)) : null
    return tick ? tick.ltp : null
  }
  const row = (each: Strike) => {
    const exchange = (each.ce ?? each.pe)?.exchange ?? 'NFO'
    const decimals = priceDecimals(exchange)
    return (
      <li
        key={each.strike}
        data-strike={each.strike}
        data-atm={each.atm || undefined}
        className={cn(
          'flex flex-col gap-2 px-4 py-3',
          each.atm && 'bg-[color:var(--primary-tint)]'
        )}
      >
        <p className="flex items-center gap-2 text-base font-bold">
          {num(each.strike)}
          {each.atm && (
            <span className="rounded-md border border-primary px-1.5 py-0.5 text-xs font-bold text-primary">
              ATM
            </span>
          )}
        </p>
        <div className="flex gap-3">
          {(['CE', 'PE'] as const).map((side) => {
            const contract = side === 'CE' ? each.ce : each.pe
            return (
              <Leg
                key={side}
                side={side}
                strike={each.strike}
                contract={contract}
                price={priceAt(contract)}
                decimals={decimals}
                added={stands(contract)}
                busy={contract !== null && busy === contract.key}
                blocked={full || busy !== null}
                onAdd={() => contract && void add(contract)}
              />
            )
          })}
        </div>
        <button
          type="button"
          disabled={rule === null || each.ce === null || each.pe === null}
          onClick={() =>
            rule &&
            props.onFollow({
              mode: 'add',
              kind: 'strike_pair',
              underlying: props.underlying,
              exchange: props.exchange,
              sectionId: null,
              fixed: { strike: each.strike, expiry: rule },
            })
          }
          aria-label={`Add as one row at ${num(each.strike)}`}
          className={OUTLINED}
        >
          Add as one row
        </button>
      </li>
    )
  }

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) props.onClose()
      }}
      title={`Pick a strike · ${props.underlying}`}
      description={`Adds to ${props.listName}`}
      className="h-[92dvh]"
    >
      <div data-strike-picker="true" className="flex h-full flex-col gap-2">
        <div className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-3 px-1">
          <p data-underlying-price="true" className="text-lg font-bold">
            {props.underlying}{' '}
            {ltp === null ? (
              <span className="text-sm font-semibold text-muted-foreground">
                waiting for its price
              </span>
            ) : (
              <span data-price-figure="true">
                {formatPrice(ltp, priceDecimals(props.exchange))}
              </span>
            )}
          </p>
          <LiveLine line={line} now={props.now} className="flex" />
        </div>

        {ladder.expiries.length > 0 && (
          <fieldset className="-mx-4 m-0 flex min-w-0 shrink-0 gap-2 overflow-x-auto border-0 px-4 py-0">
            <legend className="sr-only">Expiry</legend>
            {ladder.expiries.map((each) => {
              const on = ladder.tab === each.date
              return (
                <button
                  key={each.date}
                  type="button"
                  aria-pressed={on}
                  data-expiry={each.date}
                  onClick={() => ladder.choose(each.date)}
                  className={cn(
                    'inline-flex min-h-12 shrink-0 items-center gap-1.5 rounded-full border px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                    on
                      ? 'border-primary bg-[color:var(--primary-tint)] text-primary'
                      : 'border-input bg-card text-foreground'
                  )}
                >
                  {on && <Check aria-hidden="true" className="size-4" />}
                  {each.label}
                </button>
              )
            })}
          </fieldset>
        )}

        {ladder.ended && (
          <output
            data-expiry-ended="true"
            className="block shrink-0 rounded-[0.875rem] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--gold-ink)]"
          >
            {ladder.ended}
          </output>
        )}
        {data?.words && (
          <p
            data-strike-words="true"
            className="shrink-0 px-1 text-sm font-medium text-[color:var(--text-secondary)]"
          >
            {data.words}
          </p>
        )}
        {full && (
          <output className="block shrink-0 rounded-[0.875rem] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--gold-ink)]">
            {props.listName} is full. Remove a symbol to add another.
          </output>
        )}
        {rule === null && data && (
          <p
            data-no-rule="true"
            className="shrink-0 px-1 text-sm font-medium text-muted-foreground"
          >
            {NO_RULE}
          </p>
        )}

        <div className="grid shrink-0 grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() =>
              props.onFollow({
                mode: 'add',
                kind: 'atm_option',
                underlying: props.underlying,
                exchange: props.exchange,
                sectionId: null,
              })
            }
            className={OUTLINED}
          >
            Add ATM row
          </button>
          {props.chain ? (
            <Link to={chainPath(props.chain)} className={OUTLINED}>
              <Layers aria-hidden="true" className="size-5" />
              Open option chain
            </Link>
          ) : (
            <span />
          )}
        </div>

        {ladder.loading && (
          <p className="flex items-center gap-2 px-1 text-sm font-medium text-muted-foreground">
            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
            Reading the strikes.
          </p>
        )}
        {ladder.error && (
          <p className="flex flex-wrap items-center gap-x-2 px-1 text-sm font-medium text-[color:var(--warning-text)]">
            {ladder.error}
            <button
              type="button"
              onClick={ladder.retry}
              className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
            >
              Try again
            </button>
          </p>
        )}
        {data && (
          <ul className="-mx-4 min-h-0 flex-1 divide-y divide-[color:var(--divider)] overflow-y-auto">
            {data.strikes.map(row)}
          </ul>
        )}
      </div>
    </BottomSheet>
  )
}
