/**
 * The phone chart (M4.15, M4.16): one instrument, live tick by tick, laid out
 * for a phone held in one hand.
 *
 * From the top: an app bar with the way back and the instrument (tap it, or
 * the search button, to switch); the price, its change on the previous close
 * and whether it is real-time; the timeframes; the chart, which fills the
 * rest; and Buy and Sell, which open the app's own order ticket with the
 * product picked for the segment on this phone (the watchlist's ticket
 * remembers it), MIS until one is picked. Press and hold the chart to read a
 * candle.
 *
 * The order ticket and the instrument search are entries in the browser's
 * history, so the phone's Back closes them before it leaves the chart. A new
 * instrument takes the chart's own entry, so Back from it leaves the chart
 * rather than walking back through every instrument looked at.
 *
 * The chart itself is `PhoneChart`, on the same live-bar pipeline as /trading.
 * This file is layout and wiring only. A tick re-renders the price header and
 * nothing else: the price lives in a small store outside React's tree.
 */
import { ArrowLeft, BellPlus, Search, SunMedium, X } from 'lucide-react'
import type { Bar } from 'openalgo-charts'
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { SymbolSearchDialog } from '@/components/trading/SymbolSearchDialog'
import { Switch } from '@/components/ui/switch'
import { chartHref } from '@/lib/trading/chartLink'
import { type ChartStateView, chartStateCopy, createChartStateGate } from '@/lib/trading/chartState'
import { priceDp } from '@/lib/trading/format'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { AlertFormSheet, toastAlertSet } from '@/sudarshan/alerts/AlertForm' // SUDARSHAN M10.2
import { isCovered } from '@/sudarshan/alerts/words'
import { directionOf, formatPercent, formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { ticketProduct } from '@/sudarshan/ticket/productPref'
import { showToast } from '@/utils/toast'
import {
  type FeedConfig,
  type InstrumentInfo,
  loadFeedConfig,
  loadInstrument,
  searchSymbols,
} from './api'
import { LongPress } from './longPress'
import { isQuoteOnly, PhoneChart } from './phoneChart'
import {
  CHART_CHIPS,
  type ChartChip,
  chartPath,
  readChip,
  readLastInstrument,
  saveChip,
  saveLastInstrument,
} from './prefs'
import { PriceStore, tickLine } from './prices'
import { useWakeLock } from './useWakeLock'

/** The order tag the chart's tickets carry, the same as the /trading terminal's. */
const STRATEGY = 'chart-trading'

const barStamp = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const barDay = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})
const compact = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 2 })

const closesStamp = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** A bare /chart: the instrument last opened on this phone, or NIFTY. */
export function ChartIndex() {
  const last = readLastInstrument()
  return <Navigate replace to={chartPath(last.symbol, last.exchange)} />
}

type WakeLock = ReturnType<typeof useWakeLock>

export default function ChartPage() {
  const params = useParams()
  // Held here, above the screen that is built anew for each instrument, so
  // switching instrument keeps the screen on.
  const wake = useWakeLock()
  const symbol = params.symbol ?? ''
  const exchange = (params.exchange ?? '').toUpperCase()
  if (!symbol || !exchange) return <ChartIndex />
  return (
    <PhoneChartScreen
      key={`${exchange}:${symbol}`}
      symbol={symbol}
      exchange={exchange}
      wake={wake}
    />
  )
}

type Inspect = { bar: Bar; x: number | null; y: number }

/** A sheet over the chart, as the browser history entry it is; `alert` is a market alert's form (M10.2). */
type Sheet = { sheet: 'ticket'; side: 'BUY' | 'SELL' } | { sheet: 'search' } | { sheet: 'alert' }

function readSheet(state: unknown): Sheet | null {
  if (!state || typeof state !== 'object') return null
  const value = state as { sheet?: unknown; side?: unknown }
  if (value.sheet === 'search') return { sheet: 'search' }
  if (value.sheet === 'alert') return { sheet: 'alert' }
  if (value.sheet === 'ticket' && (value.side === 'BUY' || value.side === 'SELL'))
    return { sheet: 'ticket', side: value.side }
  return null
}

/** Whether nothing in this tab's history is behind the current page. */
function firstInHistory(key: string): boolean {
  if (key === 'default') return true
  const state = (globalThis.history?.state ?? null) as { idx?: unknown } | null
  return state?.idx === 0
}

function PhoneChartScreen({
  symbol,
  exchange,
  wake,
}: {
  symbol: string
  exchange: string
  wake: WakeLock
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const appMode = useThemeStore((s) => s.appMode)
  const [config, setConfig] = useState<FeedConfig | null | undefined>(undefined)
  const [info, setInfo] = useState<InstrumentInfo | null | undefined>(undefined)
  const [chip, setChip] = useState<ChartChip>(() => readChip())
  const [prevClose, setPrevClose] = useState<number | null>(null)
  const [status, setStatus] = useState<LiveStatus | null>(null)
  const [load, setLoad] = useState<ChartStateView | null>(null)
  const [inspect, setInspect] = useState<Inspect | null>(null)
  const [closesAt, setClosesAt] = useState<number | null>(null)
  const [owner, setOwner] = useState<PhoneChart | null>(null)
  const [prices] = useState(() => new PriceStore())
  const host = useRef<HTMLDivElement>(null)
  const surface = useRef<HTMLDivElement>(null)
  /** An instrument picked in search, opened once the search's history entry is gone. */
  const picked = useRef<{ symbol: string; exchange: string } | null>(null)
  /** The sheet's entry is already being left: a dialog that closes twice goes back once. */
  const closing = useRef(false)
  const quoteOnly = isQuoteOnly(exchange)
  const sheet = readSheet(location.state)
  const side = sheet?.sheet === 'ticket' ? sheet.side : null
  const searching = sheet?.sheet === 'search'

  useEffect(() => {
    saveLastInstrument(symbol, exchange)
  }, [symbol, exchange])

  useEffect(() => {
    let alive = true
    void loadFeedConfig().then((value) => {
      if (alive) setConfig(value)
    })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!config) return
    let alive = true
    void loadInstrument(config.apiKey, symbol, exchange).then((value) => {
      if (alive) setInfo(value)
    })
    return () => {
      alive = false
    }
  }, [config, symbol, exchange])

  // One engine host for this screen, built once the key is known.
  useEffect(() => {
    if (!config || !host.current) return
    // The same gate a /trading pane uses: a quick load never flashes its card.
    const gate = createChartStateGate(setLoad)
    const next = new PhoneChart({
      container: host.current,
      apiKey: config.apiKey,
      wsUrl: config.wsUrl,
      // The mode as it is now; a later switch reaches the chart through setAppMode.
      appMode: useThemeStore.getState().appMode,
      callbacks: {
        onTick: (tick) => prices.set(tick),
        onPrevClose: setPrevClose,
        onStatus: setStatus,
        onLoad: (state) => gate.set(state),
        onSessionClose: setClosesAt,
      },
    })
    setOwner(next)
    return () => {
      setOwner(null)
      gate.destroy()
      next.destroy()
    }
  }, [config, prices])

  useEffect(() => {
    owner?.setAppMode(appMode)
  }, [owner, appMode])

  useEffect(() => {
    if (!owner) return
    setInspect(null)
    void owner.load(symbol, exchange, chip)
  }, [owner, symbol, exchange, chip])

  // Press and hold to read a candle.
  useEffect(() => {
    const el = surface.current
    if (!el || !owner) return
    const read = (x: number, y: number) => {
      const bar = owner.barAt(x)
      if (bar) setInspect({ bar, x: owner.barX(bar), y })
    }
    const press = new LongPress(el, {
      onStart: ({ x, y }) => read(x, y),
      onMove: ({ x, y }) => read(x, y),
      onEnd: () => {},
      onClear: () => setInspect(null),
    })
    return () => press.destroy()
  }, [owner])

  const pickChip = useCallback((next: ChartChip) => {
    setChip(next)
    saveChip(next)
  }, [])

  const search = useCallback(
    (query: string, ex?: string, limit?: number) =>
      config ? searchSymbols(config.apiKey, query, ex, limit) : Promise.resolve([]),
    [config]
  )

  // Opened from a link with nothing behind it: back goes to the dashboard.
  const back = useCallback(() => {
    if (firstInHistory(location.key)) navigate('/dashboard')
    else navigate(-1)
  }, [location.key, navigate])

  // A sheet is pushed as a history entry, so the phone's Back closes it. One
  // already open is swapped rather than stacked.
  const openSheet = useCallback(
    (next: Sheet) =>
      navigate(location.pathname + location.search, {
        state: next,
        replace: readSheet(location.state) !== null,
      }),
    [location.pathname, location.search, location.state, navigate]
  )
  const closeSheet = useCallback(() => {
    if (!readSheet(location.state) || closing.current) return
    closing.current = true
    navigate(-1)
  }, [location.state, navigate])
  // Every move through history settles what is open.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the key is the trigger
  useEffect(() => {
    closing.current = false
  }, [location.key])

  // SUDARSHAN M10.2: an alert form restored with the page (a reload, the phone bringing the app
  // back) is not reopened: it opens only from a tap, as the watchlist's does.
  const restoredAlert = useRef(sheet?.sheet === 'alert')
  useEffect(() => {
    if (!restoredAlert.current) return
    restoredAlert.current = false
    closeSheet()
  }, [closeSheet])

  // The search's entry is gone: the instrument picked takes the chart's own.
  const sheetKind = sheet?.sheet ?? null
  useEffect(() => {
    const next = picked.current
    if (sheetKind || !next) return
    picked.current = null
    navigate(chartPath(next.symbol, next.exchange), { replace: true })
  }, [sheetKind, navigate])

  const dp = (ltp: number) => priceDp(info?.tickSize, ltp)

  if (config === null) {
    return (
      <Screen>
        <AppBar symbol={symbol} exchange={exchange} onBack={back} />
        <div className="m-4 rounded-2xl border bg-card p-4 text-sm shadow-[var(--shadow-card)]">
          <p className="font-semibold">The chart needs your API key.</p>
          <p className="mt-1 text-muted-foreground">
            Charts read prices with it. Create one on the API key page, then come back.
          </p>
          <Link
            to="/apikey"
            className="mt-2 inline-flex min-h-12 items-center font-semibold text-primary underline-offset-4 hover:underline"
          >
            Open the API key page
          </Link>
        </div>
      </Screen>
    )
  }

  const ordersOff = quoteOnly || !info
  return (
    <Screen>
      <AppBar
        symbol={symbol}
        exchange={exchange}
        name={info?.name}
        onBack={back}
        onSearch={() => openSheet({ sheet: 'search' })}
        onAlert={isCovered(exchange) ? () => openSheet({ sheet: 'alert' }) : undefined}
      />

      <PriceHeader
        prices={prices}
        prevClose={prevClose}
        status={status}
        dp={dp}
        traded={!quoteOnly}
        closesAt={closesAt}
      />

      <fieldset className="flex shrink-0 gap-1 border-b bg-card px-2 py-1">
        <legend className="sr-only">Timeframe</legend>
        {CHART_CHIPS.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={value === chip}
            onClick={() => pickChip(value)}
            className={cn(
              'h-12 min-w-12 flex-1 rounded-full text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              value === chip
                ? 'bg-[color:var(--primary-tint)] text-primary shadow-[inset_0_0_0_1.5px_var(--primary)]'
                : 'text-muted-foreground hover:bg-accent'
            )}
          >
            {value}
          </button>
        ))}
      </fieldset>

      <div
        ref={surface}
        className="relative min-h-0 flex-1 touch-none bg-card"
        data-testid="chart-surface"
      >
        <div ref={host} className="absolute inset-0" />
        {inspect && <Crosshair x={inspect.x} y={inspect.y} />}
        {inspect && (
          <Readout
            bar={inspect.bar}
            dp={dp(inspect.bar.close)}
            interval={chip}
            volume={!quoteOnly}
            onClose={() => setInspect(null)}
          />
        )}
        <LoadState
          view={load}
          onRetry={() => void owner?.load(symbol, exchange, chip)}
          onDismiss={() => setLoad(null)}
        />
      </div>

      <footer className="shrink-0 border-t bg-card px-3 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <div className="flex min-h-12 items-center justify-between gap-3">
          {wake.supported ? (
            <label className="flex min-h-12 items-center gap-2 text-sm font-medium">
              <SunMedium aria-hidden="true" className="size-4 text-muted-foreground" />
              Keep screen on
              <Switch checked={wake.on} onCheckedChange={wake.setOn} />
            </label>
          ) : (
            <span />
          )}
          <Link
            to={chartHref(symbol, exchange)}
            className="inline-flex min-h-12 items-center text-sm font-semibold text-primary underline-offset-4 hover:underline"
          >
            Open in full terminal
          </Link>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={ordersOff}
            onClick={() => openSheet({ sheet: 'ticket', side: 'BUY' })}
            className="h-12 rounded-xl bg-[color:var(--buy-solid)] text-base font-bold tracking-[0.06em] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-45"
          >
            BUY
          </button>
          <button
            type="button"
            disabled={ordersOff}
            onClick={() => openSheet({ sheet: 'ticket', side: 'SELL' })}
            className="h-12 rounded-xl bg-[color:var(--sell-solid)] text-base font-bold tracking-[0.06em] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-45"
          >
            SELL
          </button>
        </div>
        {quoteOnly ? (
          <p className="mt-1 text-center text-xs font-medium text-muted-foreground">
            An index is not traded. Open its future or an option to place an order.
          </p>
        ) : info === null ? (
          <p className="mt-1 text-center text-xs font-medium text-muted-foreground">
            The contract details did not load, so ordering is off here. Open the chart again to
            retry.
          </p>
        ) : null}
      </footer>

      {info && (
        <PlaceOrderDialog
          open={side !== null}
          onOpenChange={(open) => !open && closeSheet()}
          symbol={symbol}
          exchange={exchange}
          action={side ?? 'BUY'}
          lotSize={info.lotSize}
          tickSize={info.tickSize}
          // The product picked for this segment on this phone (M8.2), MIS
          // until one is: the owner trades intraday.
          product={ticketProduct(exchange) ?? 'MIS'}
          strategy={STRATEGY}
        />
      )}
      {sheet?.sheet === 'alert' && (
        <AlertFormSheet
          prefill={{
            symbol,
            exchange,
            tick: info?.tickSize ?? null,
            price: prices.get().tick?.ltp ?? null,
          }}
          onClose={closeSheet}
          onSaved={(saved) => {
            closeSheet()
            toastAlertSet(saved, navigate)
          }}
        />
      )}
      <SymbolSearchDialog
        open={searching}
        onOpenChange={(open) => !open && closeSheet()}
        search={search}
        initialQuery={symbol}
        onPick={(row) => {
          if (!row.exchange) {
            closeSheet()
            showToast.info('A computed chart opens in the full terminal.')
            return
          }
          picked.current = { symbol: String(row.symbol), exchange: String(row.exchange) }
          closeSheet()
        }}
      />
    </Screen>
  )
}

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background" data-testid="phone-chart">
      {children}
    </div>
  )
}

function AppBar({
  symbol,
  exchange,
  name,
  onBack,
  onSearch,
  onAlert,
}: {
  symbol: string
  exchange: string
  name?: string
  onBack(): void
  onSearch?(): void
  /** A market alert on this instrument (M10.2); absent where market alerts do not reach. */
  onAlert?(): void
}) {
  const title = (
    <span className="flex min-w-0 flex-col">
      <span className="truncate text-[1.0625rem] leading-tight font-semibold">{symbol}</span>
      <span className="truncate text-xs leading-tight font-medium text-muted-foreground">
        {exchange}
        {name && name !== symbol ? ` · ${name}` : ''}
      </span>
    </span>
  )
  return (
    <header className="flex h-14 shrink-0 items-center gap-1 border-b bg-card px-1">
      <button
        type="button"
        onClick={onBack}
        aria-label="Back"
        className="inline-flex size-12 shrink-0 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft aria-hidden="true" className="size-5" />
      </button>
      <h1 className="flex min-w-0 flex-1">
        {onSearch ? (
          <button
            type="button"
            onClick={onSearch}
            aria-label={`${symbol} on ${exchange}. Change instrument`}
            className="flex min-h-12 min-w-0 flex-1 items-center rounded-lg px-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {title}
          </button>
        ) : (
          <span className="flex min-h-12 min-w-0 items-center px-1">{title}</span>
        )}
      </h1>
      {onSearch && (
        <button
          type="button"
          onClick={onSearch}
          aria-label="Search instruments"
          className="inline-flex size-12 shrink-0 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Search aria-hidden="true" className="size-5" />
        </button>
      )}
      {onAlert && (
        <button
          type="button"
          onClick={onAlert}
          aria-label="Set alert"
          className="inline-flex size-12 shrink-0 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <BellPlus aria-hidden="true" className="size-5" />
        </button>
      )}
    </header>
  )
}

/**
 * The price, its change on the previous close, the feed's state and the last
 * tick's time. The only part of the page a tick re-renders. While the figures
 * are not real-time and should be (reconnecting, delayed, stale) the price is
 * drawn grey, and the chip says why.
 */
function PriceHeader({
  prices,
  prevClose,
  status,
  dp,
  traded,
  closesAt,
}: {
  prices: PriceStore
  prevClose: number | null
  status: LiveStatus | null
  dp(ltp: number): number
  /** An index is quoted, not traded. */
  traded: boolean
  /** The session's close, while the forming bar would run past it. */
  closesAt: number | null
}) {
  const { tick, streamed } = useSyncExternalStore(prices.subscribe, prices.get)
  const now = useNow()
  const ltp = tick?.ltp ?? null
  const decimals = dp(ltp ?? 0)
  const change = ltp !== null && prevClose !== null ? ltp - prevClose : null
  const percent = change !== null && prevClose ? (change / prevClose) * 100 : null
  const direction = change === null ? 'flat' : directionOf(change, decimals)
  const line = status?.line
  const quiet =
    line?.kind === 'polling' ||
    line?.kind === 'connecting' ||
    (line?.kind === 'delayed' && line.stale)
  return (
    <section aria-label="Price" className="shrink-0 border-b bg-card px-4 pt-2 pb-2">
      {/* Wraps the chip under a price too wide to share the row with it. */}
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <p
          className={cn(
            'sd-display text-[2.375rem] leading-none font-semibold',
            quiet && 'text-muted-foreground'
          )}
          data-testid="chart-ltp"
        >
          {ltp === null ? (
            <>
              <span aria-hidden="true">&mdash;</span>
              <span className="sr-only">No price yet</span>
            </>
          ) : (
            formatPrice(ltp, decimals)
          )}
        </p>
        {line && (
          <LiveLine
            line={line}
            now={now}
            className="mt-0.5 min-h-8 rounded-full border border-border bg-card px-2.5"
          />
        )}
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-3">
        <p
          className={cn('flex min-h-5 items-center gap-1 text-sm font-bold', TREND_TEXT[direction])}
          data-testid="chart-change"
        >
          {change !== null && percent !== null ? (
            <>
              <TrendArrow direction={direction} className="size-2.5" />
              {formatRupees(change, { decimals, signed: true })} ({formatPercent(percent)})
            </>
          ) : (
            <span className="font-medium text-muted-foreground">Change not available yet</span>
          )}
        </p>
        <p className="text-xs font-medium text-muted-foreground" data-testid="chart-last-tick">
          {tickLine(streamed, status, now, traded)}
          {closesAt !== null && status?.line.kind !== 'closed' && (
            <span data-testid="chart-closes">
              {' · '}Closes {closesStamp.format(new Date(closesAt * 1000))}
            </span>
          )}
        </p>
      </div>
    </section>
  )
}

/** Hair lines through the candle being read and the finger's height. */
function Crosshair({ x, y }: { x: number | null; y: number }) {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-[5]">
      {x !== null && (
        <div
          className="absolute top-0 bottom-0 w-px bg-[color:var(--text-tertiary)]"
          style={{ left: x }}
        />
      )}
      <div
        className="absolute right-0 left-0 h-px bg-[color:var(--text-tertiary)]"
        style={{ top: y }}
      />
    </div>
  )
}

/** The candle under a held finger: when, O H L C, and V where it trades. */
function Readout({
  bar,
  dp,
  interval,
  volume,
  onClose,
}: {
  bar: Bar
  dp: number
  interval: ChartChip
  volume: boolean
  onClose(): void
}) {
  const at = new Date(bar.time * 1000)
  const stamp = interval === '1D' ? barDay.format(at) : barStamp.format(at)
  const cells: Array<[string, string]> = [
    ['O', formatPrice(bar.open, dp)],
    ['H', formatPrice(bar.high, dp)],
    ['L', formatPrice(bar.low, dp)],
    ['C', formatPrice(bar.close, dp)],
  ]
  if (volume) cells.push(['V', typeof bar.volume === 'number' ? compact.format(bar.volume) : '—'])
  return (
    <div
      className="absolute top-2 right-2 left-2 z-10 rounded-xl border bg-card py-1 pr-1 pl-3 shadow-[var(--shadow-raised)]"
      data-testid="chart-readout"
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground">{stamp}</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the candle reading"
          className="inline-flex size-12 items-center justify-center rounded-full hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <dl
        className={cn(
          '-mt-2 grid gap-1 pr-2 pb-1',
          cells.length === 5 ? 'grid-cols-5' : 'grid-cols-4'
        )}
      >
        {cells.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[0.6875rem] font-semibold text-muted-foreground">{label}</dt>
            <dd className="truncate text-[0.8125rem] font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * Loading, no bars, or a failed load: the /trading pane's states and words,
 * at a phone's size. A failed load leaves the price streaming above it.
 */
function LoadState({
  view,
  onRetry,
  onDismiss,
}: {
  view: ChartStateView | null
  onRetry(): void
  onDismiss(): void
}) {
  if (!view || view.kind === 'ready') return null
  const copy = chartStateCopy(view)
  if (view.kind === 'loading')
    return (
      <div className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center">
        <p
          role={copy.role}
          className="rounded-full border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground"
        >
          {copy.title}
        </p>
      </div>
    )
  return (
    <div className="pointer-events-none absolute inset-0 z-[4] flex items-center justify-center p-4">
      <div
        role={copy.role}
        className="pointer-events-auto max-w-xs rounded-2xl border bg-card p-4 text-center shadow-[var(--shadow-card)]"
        data-chart-state={view.kind}
      >
        <p className="text-sm font-semibold">{copy.title}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{copy.text}</p>
        <div className="mt-2 flex justify-center gap-2">
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex min-h-12 items-center rounded-full px-4 text-sm font-semibold text-primary hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={onDismiss}
            className="inline-flex min-h-12 items-center rounded-full px-4 text-sm font-semibold text-muted-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}
