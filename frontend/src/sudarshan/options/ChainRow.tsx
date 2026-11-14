import { Clock } from 'lucide-react'
import { memo } from 'react'
import { cn } from '@/lib/utils'
import { directionOf, formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { formatChange, type TickDirection } from '@/sudarshan/watchlist/rows'
import { BUILD_UP_WORDS, type LegView, type StrikeView } from './model'
import { oiWords, strikeWords } from './words'

export type ChainView = 'price' | 'oi' | 'greeks'

const GREY = 'text-[color:var(--stale)]'
const SMALL = 'text-xs leading-tight font-semibold'

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

/** Everything a row draws, as one text: the row re-renders only when this changes. */
export function rowSignature(
  row: StrikeView,
  view: ChainView,
  extra: {
    ceTick: TickDirection | null
    peTick: TickDirection | null
    grey: boolean
    maxOi: number
    itm: 'ce' | 'pe' | null
  }
): string {
  const leg = (each: LegView | null) =>
    each === null
      ? '-'
      : [
          each.ltp,
          each.change?.toFixed(2),
          view === 'oi' ? `${each.oi}|${each.oiChange}|${each.buildUp}` : '',
          view === 'greeks'
            ? `${each.greeks?.delta.toFixed(2)}|${each.greeks?.theta.toFixed(2)}`
            : '',
          view !== 'oi' ? each.greeks?.iv.toFixed(1) : '',
        ].join(',')
  return [
    row.strike,
    row.atm,
    row.live,
    view,
    leg(row.ce),
    leg(row.pe),
    row.pcr?.toFixed(2),
    extra.ceTick,
    extra.peTick,
    extra.grey,
    extra.maxOi,
    extra.itm,
  ].join(';') // prettier-ignore
}

function Second({ leg, view, grey }: { leg: LegView; view: ChainView; grey: boolean }) {
  if (view === 'price') {
    if (leg.change === null || leg.changePct === null) return <span className={SMALL}>{dash}</span>
    const direction = directionOf(leg.change)
    return (
      <span
        className={cn(SMALL, 'inline-flex items-center gap-1', grey ? GREY : TREND_TEXT[direction])}
      >
        <TrendArrow direction={direction} className="size-2" />
        {formatChange(leg.change)} · {formatPercent(leg.changePct, 1)}
      </span>
    )
  }
  if (view === 'oi') {
    const direction = leg.oiChange === null ? 'flat' : directionOf(leg.oiChange, 0)
    return (
      <>
        <span className={cn(SMALL, 'inline-flex items-center gap-1', grey && GREY)}>
          <span className="sr-only">open interest</span>
          {leg.oi === null ? dash : oiWords(leg.oi)}
          {leg.oiChange !== null && (
            <span
              className={cn(
                'inline-flex items-center gap-0.5',
                grey ? GREY : TREND_TEXT[direction]
              )}
            >
              <TrendArrow direction={direction} className="size-2" />
              {direction === 'up' ? '+' : direction === 'down' ? '−' : ''}
              {oiWords(leg.oiChange)}
            </span>
          )}
        </span>
        {leg.buildUp && (
          <span
            className={cn(SMALL, 'font-medium', grey ? GREY : 'text-[color:var(--text-secondary)]')}
          >
            {BUILD_UP_WORDS[leg.buildUp]}
          </span>
        )}
      </>
    )
  }
  const greeks = leg.greeks
  return (
    <span className={cn(SMALL, grey && GREY)}>
      {greeks ? (
        <>
          <span aria-hidden="true">Δ</span>
          <span className="sr-only">delta</span> {greeks.delta.toFixed(2)}{' '}
          <span aria-hidden="true">· Θ</span>
          <span className="sr-only">theta</span> {formatChange(greeks.theta)}
        </>
      ) : (
        dash
      )}
    </span>
  )
}

function Side({
  leg,
  side,
  strike,
  view,
  tick,
  grey,
  itm,
  share,
  onOpen,
}: {
  leg: LegView | null
  side: 'ce' | 'pe'
  strike: number
  view: ChainView
  tick: TickDirection | null
  grey: boolean
  itm: boolean
  /** The strike's open interest as a share of the largest shown, 0 to 1; the bar is decoration. */
  share: number
  onOpen: (strike: number, side: 'ce' | 'pe') => void
}) {
  const call = side === 'ce'
  const name = `${strikeWords(strike)} ${call ? 'call' : 'put'}`
  if (leg === null) {
    return (
      <span
        className={cn(
          'flex min-h-16 items-center px-2 text-sm',
          call ? 'justify-start' : 'justify-end'
        )}
      >
        <span aria-hidden="true">&mdash;</span>
        <span className="sr-only">{name} is not listed</span>
      </span>
    )
  }
  return (
    <button
      type="button"
      data-leg={side.toUpperCase()}
      data-itm={itm || undefined}
      onClick={() => onOpen(strike, side)}
      className={cn(
        'relative flex min-h-16 w-full flex-col justify-center gap-0.5 overflow-hidden px-2 py-1 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
        call ? 'items-start text-left' : 'items-end text-right',
        itm && 'bg-[color:var(--surface-2)]'
      )}
    >
      {view === 'oi' && share > 0 && (
        <span
          aria-hidden="true"
          data-oi-bar="true"
          className={cn(
            'absolute inset-y-1 bg-[color:var(--primary-tint)]',
            call ? 'right-0 rounded-l-md' : 'left-0 rounded-r-md'
          )}
          style={{ width: `${Math.max(2, Math.round(share * 100))}%` }}
        />
      )}
      {itm && (
        <span
          aria-hidden="true"
          className={cn(
            'absolute top-0 size-0 border-t-[0.5rem] border-t-[color:var(--border-strong)] border-x-transparent',
            call ? 'left-0 border-r-[0.5rem]' : 'right-0 border-l-[0.5rem]'
          )}
        />
      )}
      <span className="sr-only">
        {name}
        {itm ? ', in the money' : ''}
      </span>
      <span
        data-tick={tick ?? undefined}
        className={cn(
          'relative inline-flex items-center gap-1 text-base leading-tight font-bold',
          grey ? GREY : TREND_TEXT[tick ?? 'flat']
        )}
      >
        {tick && <TrendArrow direction={tick} className="size-2.5" />}
        {leg.ltp === null ? dash : formatPrice(leg.ltp)}
      </span>
      <span className={cn('relative flex flex-col gap-0.5', call ? 'items-start' : 'items-end')}>
        <Second leg={leg} view={view} grey={grey} />
      </span>
    </button>
  )
}

export interface ChainRowProps {
  row: StrikeView
  view: ChainView
  ceTick: TickDirection | null
  peTick: TickDirection | null
  /** The figures are not current: the socket is down or stale, or the strike is not followed. */
  grey: boolean
  /** Which side is in the money at the spot; null when the spot is not known. */
  itm: 'ce' | 'pe' | null
  /** The largest open interest among the rows shown. */
  maxOi: number
  onOpen: (strike: number, side: 'ce' | 'pe') => void
  /** Set by the tab: `rowSignature` of everything above. */
  signature: string
}

/**
 * One strike of the Chain tab (M11.1, O5): 64 dp, two lines, three columns. Line 1 is last price,
 * strike, last price; line 2 follows the view. A side is one button that opens the strike sheet.
 * The at-the-money row carries the word, a side in the money a tint, a corner mark and the words
 * for a screen reader, a move an arrow and a sign: nothing is told by colour alone, and nothing
 * flashes.
 */
export const ChainRow = memo(
  function ChainRow({ row, view, ceTick, peTick, grey, itm, maxOi, onOpen }: ChainRowProps) {
    const iv = [row.ce?.greeks?.iv, row.pe?.greeks?.iv].filter(
      (each): each is number => !!each && each > 0
    )
    const share = (leg: LegView | null) => (leg?.oi && maxOi > 0 ? leg.oi / maxOi : 0)
    return (
      <li
        data-strike={row.strike}
        data-atm={row.atm || undefined}
        data-wing={!row.live || undefined}
        className={cn(
          'grid grid-cols-[minmax(0,1fr)_5.25rem_minmax(0,1fr)] items-stretch border-b border-[color:var(--divider)] [contain:layout_style]',
          row.atm && 'rounded-md border-2 border-[color:var(--brand)]'
        )}
      >
        <Side
          leg={row.ce}
          side="ce"
          strike={row.strike}
          view={view}
          tick={ceTick}
          grey={grey}
          itm={itm === 'ce'}
          share={share(row.ce)}
          onOpen={onOpen}
        />
        <span className="flex flex-col items-center justify-center gap-0.5 px-1 text-center">
          <span className="text-[0.9375rem] leading-tight font-bold">
            {strikeWords(row.strike)}
          </span>
          {row.atm && (
            <span className="rounded bg-[color:var(--primary-tint)] px-1 text-[0.6875rem] leading-tight font-bold text-primary">
              ATM
            </span>
          )}
          <span className={cn(SMALL, 'text-[color:var(--text-secondary)]')}>
            {view === 'oi' ? (
              <>PCR {row.pcr === null ? dash : row.pcr.toFixed(2)}</>
            ) : (
              <>IV {iv.length ? (iv.reduce((a, b) => a + b, 0) / iv.length).toFixed(1) : dash}</>
            )}
          </span>
          {!row.live && (
            <span className={cn(SMALL, 'inline-flex items-center gap-0.5', GREY)}>
              <Clock aria-hidden="true" className="size-3" />
              <span className="sr-only">not real-time</span>
            </span>
          )}
        </span>
        <Side
          leg={row.pe}
          side="pe"
          strike={row.strike}
          view={view}
          tick={peTick}
          grey={grey}
          itm={itm === 'pe'}
          share={share(row.pe)}
          onOpen={onOpen}
        />
      </li>
    )
  },
  (before, after) => before.signature === after.signature && before.onOpen === after.onOpen
)
