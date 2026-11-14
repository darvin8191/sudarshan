/**
 * The Straddle tab (M11.4, O8): the call + put premium of one strike, real-time, and its chart.
 *
 * Figures come from the chain model, so they move with the same ticks as the other tabs. The chart
 * is the phone's computed chart (`chart/computedChart.ts`, C11), loaded only when this tab opens:
 * history from the two legs' candles, then the two legs' ticks handed over from this screen's own
 * subscription. Nothing new is followed for a strike inside the window; a far strike's two legs
 * are, while it is shown (`straddle.ts`).
 *
 * A straddle is not an instrument: "Trade the legs" opens the strike sheet, whose Buy and Sell
 * open the app's own ticket. No order code here.
 */
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SymbolData } from '@/lib/MarketDataManager'
import { type ChartStateView, chartStateCopy } from '@/lib/trading/chartState'
import { cn } from '@/lib/utils'
import type { AppMode } from '@/stores/themeStore'
import type { ComputedChart } from '@/sudarshan/chart/computedChart'
import { directionOf, formatPercent, formatPrice } from '@/sudarshan/dashboard/format'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { formatChange } from '@/sudarshan/watchlist/rows'
import type { ChainModel } from './model'
import {
  legTick,
  stepStrike,
  straddleFigures,
  straddleFollows,
  straddleStart,
  straddleStrikes,
} from './straddle'
import { clockTime } from './TimedRefresh'
import { strikeWords } from './words'

export const STRADDLE_INTERVALS = ['1m', '5m', '15m', '1h'] as const
export const STRADDLE_INTERVAL = '5m'
export const NOT_TRADABLE = 'Computed · not tradable'

const STEP =
  'inline-flex size-12 shrink-0 items-center justify-center rounded-[0.875rem] border border-input bg-card outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40'
const OUTLINED =
  'inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring'

const dash = (
  <>
    <span aria-hidden="true">&mdash;</span>
    <span className="sr-only">not available</span>
  </>
)

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-xs font-semibold text-[color:var(--text-secondary)]">{label}</dt>
      <dd className="text-base font-bold">{children}</dd>
    </div>
  )
}

export interface StraddleTabProps {
  model: ChainModel
  /** Changes with the underlying or the expiry: the tab starts again on the money. */
  chainKey: string
  /** "NIFTY", for the chart's own words. */
  underlying: string
  /** The strike the address named ("Straddle at 25,900"). */
  startAt: number | null
  /** The strikes the window follows. */
  followed: ReadonlySet<number>
  lines: ReadonlyMap<string, SymbolData>
  /** Counts the socket's sign-ins: a change is a reconnect. */
  epoch: number
  apiKey: string
  appMode: AppMode
  dim: boolean
  /** What must be followed beside the window while this tab is shown (EXCHANGE:SYMBOL). */
  onFollow: (keys: string[]) => void
  /** Open the strike sheet, that side first. */
  onTrade: (strike: number, side: 'ce' | 'pe') => void
}

export function StraddleTab({
  model,
  chainKey,
  underlying,
  startAt,
  followed,
  lines,
  epoch,
  apiKey,
  appMode,
  dim,
  onFollow,
  onTrade,
}: StraddleTabProps) {
  const [picked, setPicked] = useState<{ key: string; strike: number } | null>(null)
  const [followAtm, setFollowAtm] = useState(false)
  const [moved, setMoved] = useState<{ key: string; words: string } | null>(null)
  const [interval, setIntervalChip] = useState<string>(STRADDLE_INTERVAL)
  const [load, setLoad] = useState<ChartStateView | null>(null)
  const [attempt, setAttempt] = useState(0)

  const strikes = useMemo(() => straddleStrikes(model), [model])
  const mine = picked !== null && picked.key === chainKey && strikes.includes(picked.strike)
  const strike = mine ? picked.strike : straddleStart(model, startAt)
  const atmStrike = model.atm !== null && strikes.includes(model.atm) ? model.atm : null
  // The strike it opened on is held: the money walking never moves the straddle under the reader.
  useEffect(() => {
    if (!mine && strike !== null) setPicked({ key: chainKey, strike })
  }, [mine, strike, chainKey])

  // "Follow ATM": the straddle re-points when the money moves, and says so. Off unless asked.
  useEffect(() => {
    if (!followAtm || atmStrike === null || strike === null || atmStrike === strike) return
    setPicked({ key: chainKey, strike: atmStrike })
    setMoved({
      key: chainKey,
      words: `ATM moved to ${strikeWords(atmStrike)} at ${clockTime(Date.now()).slice(0, 5)}`,
    })
  }, [followAtm, atmStrike, strike, chainKey])

  const row = strike === null ? undefined : model.rows.find((each) => each.strike === strike)
  const figures = straddleFigures(row, model.spot)
  const ceKey = row?.ce?.key ?? null
  const peKey = row?.pe?.key ?? null
  const ceSymbol = row?.ce?.symbol ?? null
  const peSymbol = row?.pe?.symbol ?? null
  const legExchange = row?.ce?.exchange ?? null

  // A far strike's two legs are followed while it is shown; a window strike needs nothing.
  const wanted = straddleFollows(row, followed).join(',')
  useEffect(() => {
    onFollow(wanted === '' ? [] : wanted.split(','))
  }, [wanted, onFollow])
  useEffect(() => () => onFollow([]), [onFollow])

  // -- the chart ---------------------------------------------------------------------------
  const holder = useRef<HTMLDivElement>(null)
  const chart = useRef<ComputedChart | null>(null)
  const [ready, setReady] = useState(false)
  const mode = useRef(appMode)
  mode.current = appMode
  useEffect(() => {
    let gone = false
    let made: ComputedChart | null = null
    void import('@/sudarshan/chart/computedChart')
      .then(({ ComputedChart }) => {
        if (gone || !holder.current) return
        made = new ComputedChart({
          container: holder.current,
          apiKey,
          appMode: mode.current,
          callbacks: { onLoad: (state) => !gone && setLoad(state) },
        })
        chart.current = made
        setReady(true)
      })
      .catch(() => {
        if (!gone)
          setLoad({
            kind: 'error',
            symbol: 'the straddle chart',
            interval: '',
            message: 'The chart could not be loaded. Check the connection',
          })
      })
    return () => {
      gone = true
      chart.current = null
      made?.destroy()
      setReady(false)
    }
  }, [apiKey])
  useEffect(() => {
    if (ready) chart.current?.setAppMode(appMode)
  }, [ready, appMode])

  const sent = useRef<[SymbolData | null, SymbolData | null]>([null, null])
  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` is Try again
  useEffect(() => {
    if (!ready || strike === null || !ceSymbol || !peSymbol || !legExchange) return
    sent.current = [null, null]
    void chart.current?.loadComputed(
      {
        legs: [
          { symbol: ceSymbol, exchange: legExchange },
          { symbol: peSymbol, exchange: legExchange },
        ],
        op: 'sum',
      },
      interval,
      `${underlying} ${strikeWords(strike)} straddle`
    )
  }, [ready, strike, ceSymbol, peSymbol, legExchange, interval, underlying, attempt])

  // Each leg's tick, once: the screen's own subscription is the chart's feed.
  const ceLine = ceKey ? lines.get(ceKey) : undefined
  const peLine = peKey ? lines.get(peKey) : undefined
  useEffect(() => {
    if (!ready) return
    ;[ceLine, peLine].forEach((line, leg) => {
      const tick = legTick(line)
      if (!tick || sent.current[leg] === tick.line) return
      sent.current[leg] = tick.line
      chart.current?.tick(leg, tick.price, tick.time)
    })
  }, [ready, ceLine, peLine])

  const lastEpoch = useRef(epoch)
  useEffect(() => {
    if (lastEpoch.current === epoch) return
    lastEpoch.current = epoch
    chart.current?.resync()
  }, [epoch])

  if (strike === null || !figures) {
    return (
      <p
        data-straddle-none="true"
        className="text-sm font-semibold text-[color:var(--text-secondary)]"
      >
        No strike of this expiry lists both a call and a put, so there is no straddle to show.
      </p>
    )
  }

  const pick = (next: number) => {
    setFollowAtm(false)
    setMoved(null)
    setPicked({ key: chainKey, strike: next })
  }
  const down = stepStrike(model, strike, -1)
  const up = stepStrike(model, strike, 1)
  const direction = figures.change === null ? 'flat' : directionOf(figures.change)
  const copy = load && load.kind !== 'ready' ? chartStateCopy(load) : null
  const note = moved !== null && moved.key === chainKey ? moved.words : null

  return (
    <section
      aria-label="Straddle"
      data-straddle-strike={strike}
      className={cn('flex flex-col gap-3', dim && 'text-[color:var(--stale)]')}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="Lower strike"
          disabled={down === strike}
          onClick={() => pick(down)}
          className={STEP}
        >
          <ChevronDown aria-hidden="true" className="size-5" />
        </button>
        <p className="flex min-w-0 flex-1 flex-col items-center text-center">
          <span className="sd-display text-[1.375rem] leading-tight font-bold">
            {strikeWords(strike)}
          </span>
          <span className="text-xs font-semibold text-[color:var(--text-secondary)]">
            {figures.atm ? 'ATM straddle' : 'Straddle'}
            {!figures.live && ' · a far strike, followed while shown'}
          </span>
        </p>
        <button
          type="button"
          aria-label="Higher strike"
          disabled={up === strike}
          onClick={() => pick(up)}
          className={STEP}
        >
          <ChevronUp aria-hidden="true" className="size-5" />
        </button>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={followAtm}
        disabled={atmStrike === null}
        onClick={() => {
          setMoved(null)
          setFollowAtm(!followAtm)
        }}
        className={cn(
          'flex min-h-12 w-full items-center gap-3 rounded-xl border border-input px-3 text-left text-[0.9375rem] font-semibold outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
          followAtm && 'bg-[color:var(--primary-tint)] font-bold'
        )}
      >
        Follow ATM
        <span aria-hidden="true" className="ml-auto text-sm font-bold">
          {followAtm ? 'On' : 'Off'}
        </span>
      </button>

      <dl
        data-straddle-facts="true"
        className="grid grid-cols-2 gap-x-3 gap-y-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
      >
        <Fact label="Call + put">
          <span data-straddle-premium="true">
            {figures.premium === null ? dash : formatPrice(figures.premium)}
          </span>
        </Fact>
        <Fact label="Change today">
          {figures.change === null || figures.changePct === null ? (
            dash
          ) : (
            <span
              data-straddle-change="true"
              className={cn('inline-flex items-center gap-1', !dim && TREND_TEXT[direction])}
            >
              <TrendArrow direction={direction} className="size-2.5" />
              {formatChange(figures.change)} · {formatPercent(figures.changePct, 1)}
            </span>
          )}
        </Fact>
        <Fact label="Break-evens">
          {figures.lower === null || figures.upper === null ? (
            dash
          ) : (
            <span data-straddle-breakevens="true">
              {formatPrice(figures.lower)} and {formatPrice(figures.upper)}
            </span>
          )}
        </Fact>
        <Fact label={figures.atm ? 'Expected move' : 'Premium, of the spot'}>
          {figures.ofSpotPct === null ? (
            dash
          ) : (
            <span data-straddle-move="true">
              {figures.atm && '±'}
              {figures.ofSpotPct.toFixed(2)}%
            </span>
          )}
        </Fact>
      </dl>

      <fieldset className="grid grid-cols-4 gap-1 rounded-[0.875rem] bg-[color:var(--surface-2)] p-1">
        <legend className="sr-only">Chart interval</legend>
        {STRADDLE_INTERVALS.map((each) => (
          <button
            key={each}
            type="button"
            aria-pressed={interval === each}
            onClick={() => setIntervalChip(each)}
            className={cn(
              'min-h-12 rounded-[0.625rem] text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
              interval === each
                ? 'bg-card font-bold text-primary underline underline-offset-4 shadow-[var(--shadow-card)]'
                : 'text-[color:var(--text-secondary)]'
            )}
          >
            {each}
          </button>
        ))}
      </fieldset>
      <figure className="rounded-[1.125rem] border bg-card p-2 shadow-[var(--shadow-card)]">
        <figcaption className="flex flex-wrap items-center justify-between gap-x-3 px-1 pb-1 text-xs font-semibold text-[color:var(--text-secondary)]">
          <span>
            Call + put{' '}
            <span className="font-bold text-foreground">
              {figures.premium === null ? '—' : formatPrice(figures.premium)}
            </span>
          </span>
          <span data-not-tradable="true">{NOT_TRADABLE}</span>
        </figcaption>
        {note && (
          <output data-atm-moved="true" className="block px-1 pb-1 text-xs font-bold">
            {note}
          </output>
        )}
        <div className="relative h-64">
          <div ref={holder} data-straddle-chart="true" className="absolute inset-0" />
          {copy && load?.kind === 'loading' && (
            <p
              role={copy.role}
              className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs font-medium text-muted-foreground"
            >
              {copy.title}
            </p>
          )}
          {copy && load && load.kind !== 'loading' && (
            <div
              role={copy.role}
              data-chart-state={load.kind}
              className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-card/90 p-3 text-center"
            >
              <p className="text-sm font-semibold">{copy.title}</p>
              <p className="text-xs leading-relaxed text-muted-foreground">{copy.text}</p>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="inline-flex min-h-12 items-center rounded-full px-4 text-sm font-semibold text-primary outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
              >
                Try again
              </button>
            </div>
          )}
        </div>
      </figure>

      <section aria-labelledby="sd-straddle-trade" className="flex flex-col gap-2">
        <h3 id="sd-straddle-trade" className="text-sm font-bold">
          Trade the legs
        </h3>
        <div className="grid grid-cols-2 gap-2">
          <button type="button" onClick={() => onTrade(strike, 'ce')} className={OUTLINED}>
            {strikeWords(strike)} call
          </button>
          <button type="button" onClick={() => onTrade(strike, 'pe')} className={OUTLINED}>
            {strikeWords(strike)} put
          </button>
        </div>
      </section>
    </section>
  )
}
