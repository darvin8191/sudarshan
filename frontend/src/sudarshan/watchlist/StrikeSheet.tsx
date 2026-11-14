import { Layers, MoreHorizontal, Pencil, Trash2 } from 'lucide-react'
import { Link } from 'react-router'
import type { SymbolData } from '@/lib/MarketDataManager'
import { cn } from '@/lib/utils'
import { formatAmount, formatPrice } from '@/sudarshan/dashboard/format'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { FeedState } from '@/sudarshan/live/useFrameLines'
import { ticketProduct } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from './BottomSheet'
import { dayLabel } from './followRows'
import { priceDecimals, statusLine } from './rows'
import { chainPath, type OrderRequest } from './SymbolSheet'
import { NO_IV, pairGreeks, straddleOf } from './strikeSheet'
import type { LiveRow } from './useLiveRows'
import type { Contract } from './v2/types'
import { Figures, figuresOf } from './WatchlistRows'

const ACTION =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
const OUTLINED = cn(ACTION, 'border border-input bg-card text-foreground hover:bg-accent')

/** The two legs the sheet opened with, each with the contract it stands on and what is live of it. */
export interface Legs {
  ce: { contract: Contract; live: LiveRow | null }
  pe: { contract: Contract; live: LiveRow | null }
}

/** One later expiry's pair, with what is live of each side. */
export interface OtherRow {
  expiry: string
  ce: LiveRow | null
  pe: LiveRow | null
}

export interface StrikeSheetProps {
  title: string
  detail: string
  chip: string
  help: string | null
  /** The server's words when the row has no contract to show (a side missing, waiting). */
  words: string | null
  moved: { words: string; onShow: () => void } | null
  strike: number | null
  expiry: string | null
  legs: Legs | null
  lines: ReadonlyMap<string, SymbolData>
  /** Absent where the same strike's other expiries are not read (the options screen without the watchlists switch): no section. */
  others?: { state: 'loading' | 'error' | 'ready'; rows: OtherRow[]; retry: () => void }
  chain: { exchange: string; underlying: string } | null
  feed: FeedState
  now: number
  marketOpen: boolean | null
  onClose: () => void
  onOrder: (order: OrderRequest) => void
  /** A watchlist row's own two buttons; absent where the sheet is not a row's (a chain's strike), and then not drawn. */
  onEdit?: () => void
  onRemove?: () => void
  /** The leg's own sheet (chart, alert, depth): a button a leg, drawn only when given. */
  onLeg?: (side: 'ce' | 'pe', contract: Contract) => void
  /** The side that leads every pair of columns and buttons: the one tapped. CE unless said. */
  first?: 'ce' | 'pe'
}

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

const num = (value: number | undefined | null, decimals: number) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(decimals) : null

function Row({ label, pair }: { label: string; pair: [React.ReactNode, React.ReactNode] }) {
  return (
    <tr>
      <th
        scope="row"
        className="py-2 text-left text-xs font-semibold text-[color:var(--text-secondary)]"
      >
        {label}
      </th>
      <td className="px-1 py-2 text-right text-sm font-semibold">{pair[0] ?? dash}</td>
      <td className="px-1 py-2 text-right text-sm font-semibold">{pair[1] ?? dash}</td>
    </tr>
  )
}

/**
 * The sheet of a strike row (M8.11, F12): both legs' price, change, open interest, IV and Greeks (worked
 * out here by `lib/optionGreeks.ts`), the straddle, Buy and Sell on each leg through the existing order
 * ticket, the same strike on the next expiries (ticking while the sheet is open), and the option chain.
 * It stays on the contracts it opened with: when the row moves it says so, with Show. Open interest
 * change is not shown.
 *
 * Four things a host that is not a watchlist row may ask (the options screen): leave `onEdit` and
 * `onRemove` out and the row's two buttons are not drawn; leave `others` out and there is no "other
 * expiries" section; give `onLeg` for a button a leg to that leg's own sheet; give `first` to lead
 * with the side that was tapped. A caller that gives none of them gets the sheet as it always was.
 */
export function StrikeSheet(props: StrikeSheetProps) {
  const { legs, lines, strike, expiry, others } = props
  const sides: readonly ('ce' | 'pe')[] = props.first === 'pe' ? ['pe', 'ce'] : ['ce', 'pe']
  /** A pair of figures, the leading side first. */
  const pair = <T,>(ce: T, pe: T): [T, T] => (sides[0] === 'ce' ? [ce, pe] : [pe, ce])
  const ceData = legs ? lines.get(legs.ce.contract.key)?.data : undefined
  const peData = legs ? lines.get(legs.pe.contract.key)?.data : undefined
  const greeks =
    legs && strike !== null && expiry
      ? pairGreeks({ strike, expiry, ce: ceData, pe: peData })
      : null
  const straddle = straddleOf(legs?.ce.live?.view.ltp ?? null, legs?.pe.live?.view.ltp ?? null)
  const first = legs?.ce.live ?? null
  const line = statusLine({
    now: props.now,
    marketOpen: props.marketOpen,
    lastTickAt: first?.tickAt ?? null,
    lastUpdateAt: first?.view.at ?? null,
    connected: props.feed.connected,
    fallback: props.feed.fallback,
  })
  const order = (contract: Contract, action: 'BUY' | 'SELL') => {
    if (contract.lot === null) return
    props.onOrder({
      item: { id: 0, symbol: contract.symbol, exchange: contract.exchange, position: 0 },
      action,
      contract: { lotSize: contract.lot, tickSize: contract.tick ?? 0.05 },
      product: ticketProduct(contract.exchange),
    })
  }
  const g = (side: 'ce' | 'pe') => greeks?.[side] ?? null
  const iv = (side: 'ce' | 'pe') => {
    const value = g(side)?.iv
    return value ? `${value.toFixed(2)}%` : null
  }
  const oi = (data: typeof ceData) =>
    typeof data?.oi === 'number' ? formatAmount(data.oi, 0) : null

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) props.onClose()
      }}
      title={props.title}
      description={props.detail}
    >
      <div data-strike-sheet="true" className="flex flex-col gap-3">
        {legs && <LiveLine line={line} now={props.now} className="flex" />}
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-[color:var(--text-secondary)]">
          <span className="rounded-md bg-[color:var(--primary-tint)] px-1.5 py-0.5 text-xs font-bold text-primary">
            {props.chip}
          </span>
          {props.help}
        </p>
        {props.moved && (
          <output className="flex items-center justify-between gap-2 rounded-[0.875rem] bg-[color:var(--surface-2)] px-3 py-2">
            <span className="text-sm font-semibold">{props.moved.words}</span>
            <button type="button" onClick={props.moved.onShow} className={cn(OUTLINED, 'shrink-0')}>
              Show
            </button>
          </output>
        )}
        {!legs && props.words && (
          <p data-strike-words="true" className="text-sm font-semibold">
            {props.words}
          </p>
        )}

        {legs && (
          <>
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="text-xs font-bold text-[color:var(--text-tertiary)]">
                  <th scope="col" className="w-20 text-left" />
                  {sides.map((side) => (
                    <th key={side} scope="col" className="text-right">
                      {side.toUpperCase()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[color:var(--divider)]">
                <tr>
                  <th
                    scope="row"
                    className="py-2 text-left text-xs font-semibold text-[color:var(--text-secondary)]"
                  >
                    Price and change
                  </th>
                  {sides.map((side) => {
                    const live = legs[side].live
                    return (
                      <td key={side} data-leg={side.toUpperCase()} className="px-1 py-2">
                        {live ? (
                          <Figures
                            {...figuresOf(live)}
                            decimals={priceDecimals(live.item.exchange)}
                          />
                        ) : (
                          <span className="block text-right">{dash}</span>
                        )}
                      </td>
                    )
                  })}
                </tr>
                <Row label="OI" pair={pair(oi(ceData), oi(peData))} />
                {greeks ? (
                  <>
                    <Row label="IV" pair={pair(iv('ce'), iv('pe'))} />
                    <Row
                      label="Delta"
                      pair={pair(num(g('ce')?.delta, 3), num(g('pe')?.delta, 3))}
                    />
                    <Row
                      label="Theta"
                      pair={pair(num(g('ce')?.theta, 2), num(g('pe')?.theta, 2))}
                    />
                    <Row
                      label="Gamma"
                      pair={pair(num(g('ce')?.gamma, 5), num(g('pe')?.gamma, 5))}
                    />
                    <Row label="Vega" pair={pair(num(g('ce')?.vega, 2), num(g('pe')?.vega, 2))} />
                  </>
                ) : (
                  <tr>
                    <td
                      colSpan={3}
                      data-no-iv="true"
                      className="py-2 text-sm font-medium text-[color:var(--text-secondary)]"
                    >
                      {NO_IV}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <p data-straddle="true" className="text-sm font-bold">
              {straddle === null ? 'Straddle —' : `Straddle ${formatPrice(straddle, 2)}`}
            </p>

            <div className="grid grid-cols-2 gap-2">
              {sides.flatMap((side) =>
                (['BUY', 'SELL'] as const).map((action) => {
                  const contract = legs[side].contract
                  return (
                    <button
                      key={`${action}-${side}`}
                      type="button"
                      disabled={contract.lot === null}
                      onClick={() => order(contract, action)}
                      className={cn(
                        ACTION,
                        'font-bold tracking-wide text-white hover:opacity-90 disabled:opacity-50',
                        action === 'BUY'
                          ? 'bg-[color:var(--buy-solid)]'
                          : 'bg-[color:var(--sell-solid)]'
                      )}
                    >
                      {`${action === 'BUY' ? 'Buy' : 'Sell'} ${side.toUpperCase()}`}
                    </button>
                  )
                })
              )}
            </div>
            {props.onLeg && (
              <div className="grid grid-cols-2 gap-2">
                {sides.map((side) => (
                  <button
                    key={side}
                    type="button"
                    data-leg-more={side.toUpperCase()}
                    onClick={() => props.onLeg?.(side, legs[side].contract)}
                    className={OUTLINED}
                  >
                    <MoreHorizontal aria-hidden="true" className="size-5" />
                    {`${side.toUpperCase()}: chart, alert, depth`}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {legs && others && (
          <section aria-labelledby="ss-others" className="flex flex-col gap-1">
            <h3 id="ss-others" className="text-sm font-bold">
              Same strike, other expiries
            </h3>
            {others.state === 'loading' && (
              <p className="text-sm font-medium text-muted-foreground">
                Reading the other expiries.
              </p>
            )}
            {others.state === 'error' && (
              <p className="flex flex-wrap items-center gap-x-2 text-sm font-medium text-[color:var(--warning-text)]">
                The other expiries could not be read.
                <button
                  type="button"
                  onClick={others.retry}
                  className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
                >
                  Try again
                </button>
              </p>
            )}
            {others.state === 'ready' && others.rows.length === 0 && (
              <p className="text-sm font-medium text-muted-foreground">
                No later expiry lists this strike.
              </p>
            )}
            {others.rows.map((each) => (
              <div
                key={each.expiry}
                data-other-expiry={each.expiry}
                className="grid grid-cols-[4.5rem_1fr_1fr] items-center gap-2 border-t border-[color:var(--divider)] py-1"
              >
                <span className="text-xs font-semibold text-[color:var(--text-secondary)]">
                  {dayLabel(each.expiry).replace(/ \d{4}$/, '')}
                </span>
                {sides.map((side) => {
                  const live = each[side]
                  return (
                    <span key={side} data-other-leg={side.toUpperCase()}>
                      {live ? (
                        <Figures
                          {...figuresOf(live)}
                          decimals={priceDecimals(live.item.exchange)}
                        />
                      ) : (
                        <span className="block text-right">{dash}</span>
                      )}
                    </span>
                  )
                })}
              </div>
            ))}
          </section>
        )}

        <div className="grid grid-cols-2 gap-2">
          {props.chain && (
            <Link to={chainPath(props.chain)} className={cn(OUTLINED, 'col-span-2')}>
              <Layers aria-hidden="true" className="size-5" />
              Open option chain
            </Link>
          )}
          {props.onEdit && (
            <button type="button" onClick={props.onEdit} className={OUTLINED}>
              <Pencil aria-hidden="true" className="size-5" />
              Edit row
            </button>
          )}
          {props.onRemove && (
            <button
              type="button"
              onClick={props.onRemove}
              className={cn(OUTLINED, 'text-[color:var(--down)]')}
            >
              <Trash2 aria-hidden="true" className="size-5" />
              Remove row
            </button>
          )}
        </div>
      </div>
    </BottomSheet>
  )
}
