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
import {
  AlertTriangle,
  ArrowLeft,
  BellPlus,
  ChevronDown,
  RefreshCw,
  Search,
  SunMedium,
  X,
} from 'lucide-react'
import type { Bar } from 'openalgo-charts'
import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { SymbolSearchDialog } from '@/components/trading/SymbolSearchDialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { chartHref } from '@/lib/trading/chartLink'
import { type ChartStateView, chartStateCopy, createChartStateGate } from '@/lib/trading/chartState'
import { drawToolIcon } from '@/lib/trading/drawTools'
import { priceDp } from '@/lib/trading/format'
import { ChromeIcon } from '@/lib/trading/glyphs'
import { type IntervalGroup, pickInterval as offeredInterval } from '@/lib/trading/intervals'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { type PriceAccess, usePriceAccess } from '@/sudarshan/access'
import { AlertFormSheet, toastAlertSet } from '@/sudarshan/alerts/AlertForm' // SUDARSHAN M10.2
import { isCovered } from '@/sudarshan/alerts/words'
import { addProduct } from '@/sudarshan/books/cards/PositionCards'
import { formatPercent, formatPrice, formatRupees } from '@/sudarshan/dashboard/format'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { ticketProduct } from '@/sudarshan/ticket/productPref'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { useOpenGuard } from '@/sudarshan/watchlist/openGuard'
import { showToast } from '@/utils/toast'
import {
  type FeedConfig,
  type InstrumentInfo,
  loadFeedConfig,
  loadInstrument,
  loadIntervals,
  loadPreviousOi,
  searchSymbols,
} from './api'
import { DepthSheet, DepthStrip } from './DepthStrip'
import { DrawingSheet } from './DrawingSheet'
import { DRAW_ROW, DrawRow } from './DrawRow'
import { IndicatorSettingsSheet } from './IndicatorSettingsSheet'
import { IndicatorSheet } from './IndicatorSheet'
import { LineLabels } from './LineLabels'
import { LineSheet } from './LineSheet'
import { LongPress } from './longPress'
import { isQuoteOnly, PhoneChart } from './phoneChart'
import {
  type CatalogueEntry,
  COMMON_STUDIES,
  hasOpenInterest,
  type Placement,
  REFUSED,
  refusal,
  type StudyEntry,
  VOLUME_MA,
  VOLUME_MA_LENGTH,
} from './phoneStudies'
import {
  type ChartStyle,
  chartPath,
  intervalLabel,
  isCalendarInterval,
  readInterval,
  readLastInstrument,
  readPinned,
  readStudies,
  readStyle,
  saveInterval,
  saveLastInstrument,
  savePinned,
  saveStudies,
  saveStyle,
  styleOf,
  togglePin,
} from './prefs'
import { changeOn, PriceStore, priceQuiet, tickLine } from './prices'
import { SidewaysStrip } from './SidewaysStrip'
import { StudyLegend } from './StudyLegend'
import { StyleSheet } from './StyleSheet'
import { TimeframeSheet } from './TimeframeSheet'
import { useChartBooks, useChartLines } from './useChartLines'
import { usePhoneSideways } from './usePhoneLayout'
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

type Product = 'MIS' | 'NRML' | 'CNC'
const PRODUCTS: readonly Product[] = ['MIS', 'NRML', 'CNC']

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

/** A candle being read, and the price at the finger's height on the candles' pane (C10). */
type Inspect = { bar: Bar; x: number | null; y: number; price: number | null }

/**
 * A sheet over the chart, as the browser history entry it is; `alert` is a
 * market alert's form (M10.2), on the alert of `id` when a line opened it. A
 * ticket opened at a held price (C10) carries it, and one opened to add to a
 * position carries the position's product (C6). `line` is a chart line's sheet.
 */
type Sheet =
  | { sheet: 'ticket'; side: 'BUY' | 'SELL'; price?: number; product?: Product }
  | { sheet: 'search' }
  | { sheet: 'alert'; id?: number }
  | { sheet: 'line'; ids: string[] }
  | { sheet: 'note'; id: string }
  | { sheet: 'timeframe' }
  | { sheet: 'style' }
  | { sheet: 'indicators' }
  | { sheet: 'study'; index: number }
  | { sheet: 'depth' }

function readSheet(state: unknown): Sheet | null {
  if (!state || typeof state !== 'object') return null
  const value = state as {
    sheet?: unknown
    side?: unknown
    price?: unknown
    product?: unknown
    id?: unknown
    ids?: unknown
  }
  if (value.sheet === 'search') return { sheet: 'search' }
  if (value.sheet === 'note' && typeof value.id === 'string') return { sheet: 'note', id: value.id }
  if (value.sheet === 'alert')
    return typeof value.id === 'number' ? { sheet: 'alert', id: value.id } : { sheet: 'alert' }
  if (
    value.sheet === 'line' &&
    Array.isArray(value.ids) &&
    value.ids.length > 0 &&
    value.ids.every((id) => typeof id === 'string')
  )
    return { sheet: 'line', ids: value.ids as string[] }
  if (value.sheet === 'timeframe') return { sheet: 'timeframe' }
  if (value.sheet === 'style') return { sheet: 'style' }
  if (value.sheet === 'indicators') return { sheet: 'indicators' }
  if (value.sheet === 'depth') return { sheet: 'depth' }
  const index = (value as { index?: unknown }).index
  if (value.sheet === 'study' && typeof index === 'number' && Number.isInteger(index) && index >= 0)
    return { sheet: 'study', index }
  if (value.sheet === 'ticket' && (value.side === 'BUY' || value.side === 'SELL')) {
    const product = PRODUCTS.find((one) => one === value.product)
    return {
      sheet: 'ticket',
      side: value.side,
      ...(typeof value.price === 'number' && Number.isFinite(value.price) && value.price > 0
        ? { price: value.price }
        : {}),
      ...(product ? { product } : {}),
    }
  }
  return null
}

/** Whether nothing in this tab's history is behind the current page. */
function firstInHistory(key: string): boolean {
  if (key === 'default') return true
  const state = (globalThis.history?.state ?? null) as { idx?: unknown } | null
  return state?.idx === 0
}

interface ScreenProps {
  symbol: string
  exchange: string
  wake: WakeLock
}

/**
 * The chart reads with the API key and today's Upstox login
 * (src/sudarshan/access.ts), asked here, in a frame of its own: what that
 * asks through, the app's market data context, is a new value every second
 * (its page-visibility clock), and the screen below, sheets and all, would
 * otherwise draw itself again with it every second, frames a phone with five
 * studies cannot spare (C14). The screen draws again when the access changes.
 */
function PhoneChartScreen(props: ScreenProps) {
  const access = usePriceAccess()
  return <ChartScreen {...props} access={access} />
}

const ChartScreen = memo(function ChartScreen({
  symbol,
  exchange,
  wake,
  access,
}: ScreenProps & { access: PriceAccess }) {
  const navigate = useNavigate()
  const location = useLocation()
  const appMode = useThemeStore((s) => s.appMode)
  const ready = access.state === 'ready'
  /** Counts Try again after the stream settings could not be read. */
  const [attempt, setAttempt] = useState(0)
  const [config, setConfig] = useState<FeedConfig | null | undefined>(undefined)
  const [info, setInfo] = useState<InstrumentInfo | null | undefined>(undefined)
  const [timeframe, setTimeframe] = useState(() => readInterval())
  const [pinned, setPinned] = useState(() => readPinned())
  /** What the broker offers, once read; null until then, or when it could not be. */
  const [groups, setGroups] = useState<IntervalGroup[] | null>(null)
  /** Why the last pin was refused, shown in the timeframe sheet. */
  const [pinNotice, setPinNotice] = useState<string | null>(null)
  const [style, setStyle] = useState<ChartStyle>(() => readStyle())
  /** The style as it is now, for a chart built later; a change reaches a built one through setStyle. */
  const styleNow = useRef(style)
  styleNow.current = style
  /** The indicator set (C3), the phone's, for every instrument. */
  const [studies, setStudies] = useState<StudyEntry[]>(() => readStudies())
  const [studyNotice, setStudyNotice] = useState<string | null>(null)
  /** Counts the chart's study changes, so the sheet and the legend read them again. */
  const [, setStudyVersion] = useState(0)
  /** Every study on offer (C4), once the indicator tier is in, and the owner's files that failed. */
  const [catalogue, setCatalogue] = useState<CatalogueEntry[] | null>(null)
  const [scriptProblems, setScriptProblems] = useState<string[]>([])
  /** A contract's open interest at yesterday's close (C9), for the header's change. */
  const [prevOi, setPrevOi] = useState<number | null>(null)
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
  /** The price a ticket opened from a candle reading starts at, as a limit (C10). */
  const heldPrice = sheet?.sheet === 'ticket' ? sheet.price : undefined
  /** The product of the position a ticket adds to (C6): its own, so the order adds to it. */
  const addTo = sheet?.sheet === 'ticket' ? sheet.product : undefined
  const searching = sheet?.sheet === 'search'
  const sideways = usePhoneSideways()
  /** The drawing tools are over the chart (C7); a held finger then places, not reads. */
  const [drawing, setDrawing] = useState(false)
  /** Asks for a placed note's words, as the page is now. */
  const askNote = useRef<(id: string) => void>(() => {})

  useEffect(() => {
    saveLastInstrument(symbol, exchange)
  }, [symbol, exchange])

  // Read once prices can be read at all; until then the chart is not built, and
  // a chart built before is let go, so it is built again on a fresh host.
  // biome-ignore lint/correctness/useExhaustiveDependencies: Try again is the trigger
  useEffect(() => {
    if (!ready) {
      setConfig(undefined)
      return
    }
    let alive = true
    void loadFeedConfig().then((value) => {
      if (alive) setConfig(value)
    })
    return () => {
      alive = false
    }
  }, [ready, attempt])

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
      style: styleNow.current,
      callbacks: {
        onTick: (tick) => prices.set(tick),
        onPrevClose: setPrevClose,
        onStatus: setStatus,
        onLoad: (state) => gate.set(state),
        onSessionClose: setClosesAt,
        onStudies: () => setStudyVersion((n) => n + 1),
        onDepth: (depth) => prices.setDepth(depth),
        onDrawNote: (id) => askNote.current(id),
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
    void owner?.setStyle(style)
  }, [owner, style])

  useEffect(() => {
    void owner?.setStudies(studies)
  }, [owner, studies])

  useEffect(() => {
    if (owner && info) owner.setTick(info.tickSize)
  }, [owner, info])

  // Once per contract opened: the open interest the header's change runs from.
  useEffect(() => {
    if (!config || !hasOpenInterest(exchange)) return
    let alive = true
    void loadPreviousOi(config.apiKey, symbol, exchange).then((value) => {
      if (alive) setPrevOi(value)
    })
    return () => {
      alive = false
    }
  }, [config, symbol, exchange])

  useEffect(() => {
    if (!config) return
    let alive = true
    void loadIntervals(config.apiKey).then((value) => {
      if (alive) setGroups(value)
    })
    return () => {
      alive = false
    }
  }, [config])

  // A timeframe this broker does not offer (saved on another one) gives way to one it does.
  useEffect(() => {
    if (!groups) return
    const offered = offeredInterval(groups, timeframe)
    if (offered !== timeframe) setTimeframe(offered)
  }, [groups, timeframe])

  useEffect(() => {
    if (!owner) return
    setInspect(null)
    void owner.load(symbol, exchange, timeframe)
  }, [owner, symbol, exchange, timeframe])

  // The instrument's position, working orders and price alerts, as lines (C5). The 45 s
  // re-read of the books is held while the market is known to be shut.
  const shut = useRef(false)
  shut.current = status?.line.kind === 'closed'
  const marketOpen = useCallback(() => (shut.current ? false : null), [])
  const books = useChartBooks({
    apiKey: config?.apiKey ?? null,
    enabled: !quoteOnly,
    marketOpen,
  })
  const drawn = useChartLines({
    symbol,
    exchange,
    decimals: priceDp(info?.tickSize, 0),
    books,
  })
  useEffect(() => {
    owner?.setOverlays({ lines: drawn.lines })
  }, [owner, drawn.lines])

  // Press and hold to read a candle; not while the drawing tools are out.
  useEffect(() => {
    const el = surface.current
    if (!el || !owner || drawing) return
    const read = (x: number, y: number) => {
      const bar = owner.barAt(x)
      if (bar) setInspect({ bar, x: owner.barX(bar), y, price: owner.priceAt(y) })
    }
    const press = new LongPress(el, {
      onStart: ({ x, y }) => read(x, y),
      onMove: ({ x, y }) => read(x, y),
      onEnd: () => {},
      onClear: () => setInspect(null),
    })
    return () => press.destroy()
  }, [owner, drawing])

  const pickChip = useCallback((next: string) => {
    setTimeframe(next)
    saveInterval(next)
  }, [])

  /** Add a study to the set, unless five are there or it would make a third pane. */
  const addStudy = useCallback(
    (id: string) => {
      const placementOf = (study: string): Placement =>
        COMMON_STUDIES.find((c) => c.id === study)?.placement ??
        owner?.studyFacts(study)?.placement ??
        'onchart'
      const refused = refusal(studies, placementOf(id), placementOf)
      setStudyNotice(refused ? REFUSED[refused] : null)
      if (refused) return
      const next = [
        ...studies,
        { id, settings: id === VOLUME_MA ? { length: VOLUME_MA_LENGTH } : {} },
      ]
      setStudies(next)
      saveStudies(next)
    },
    [owner, studies]
  )

  /** A study's settings changed (C4), or went back to its defaults (`null`): for every instrument. */
  const changeStudy = useCallback(
    (index: number, patch: Record<string, unknown> | null) => {
      const entry = studies[index]
      if (!entry) return
      const settings =
        patch === null
          ? entry.id === VOLUME_MA
            ? { length: VOLUME_MA_LENGTH }
            : {}
          : { ...entry.settings, ...patch }
      const next = studies.map((e, i) => (i === index ? { id: e.id, settings } : e))
      setStudies(next)
      saveStudies(next)
    },
    [studies]
  )

  /** The pinned timeframes the broker offers, which are the chips. */
  const chips = useMemo(
    () => (groups ? pinned.filter((value) => groups.some((g) => g.items.includes(value))) : pinned),
    [groups, pinned]
  )

  const flipPin = useCallback(
    (next: string) => {
      const result = togglePin(chips, next)
      setPinNotice(
        result.refused === 'full'
          ? 'Six timeframes are pinned. Unpin one to pin another.'
          : result.refused === 'last'
            ? 'One timeframe stays pinned above the chart.'
            : null
      )
      if (result.refused) return
      setPinned(result.pinned)
      savePinned(result.pinned)
    },
    [chips]
  )

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
  askNote.current = (id) => openSheet({ sheet: 'note', id })
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

  // Checking the login, Upstox not logged in today, or no API key: the same
  // words as every other screen, in place of the chart.
  if (!ready) {
    return (
      <Screen>
        <AppBar symbol={symbol} exchange={exchange} onBack={back} />
        <AccessNotice access={access} className="m-4" />
      </Screen>
    )
  }

  // There is a key and a login, so the stream's settings did not come back:
  // most often the phone's connection, which the owner can check himself.
  if (config === null) {
    return (
      <Screen>
        <AppBar symbol={symbol} exchange={exchange} onBack={back} />
        <div className="m-4 flex flex-col items-start rounded-2xl border bg-card px-4 pt-4 pb-1 text-sm shadow-[var(--shadow-card)]">
          <p className="flex items-start gap-2 font-semibold">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
            />
            The chart could not start.
          </p>
          <p className="mt-1 text-muted-foreground">
            Check the phone's connection, then try again.
          </p>
          <button
            type="button"
            onClick={() => {
              setConfig(undefined)
              setAttempt((count) => count + 1)
            }}
            className="inline-flex min-h-12 items-center gap-2 font-semibold text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
        </div>
      </Screen>
    )
  }

  const ordersOff = quoteOnly || !info
  const openTicket = (side: 'BUY' | 'SELL', price?: number) =>
    openSheet(price === undefined ? { sheet: 'ticket', side } : { sheet: 'ticket', side, price })
  const openIndicators = () => {
    setStudyNotice(null)
    openSheet({ sheet: 'indicators' })
    if (!owner) return
    // The built-ins first, then the owner's own files, as the terminal loads them.
    void owner.loadIndicatorTier().then(async (ready) => {
      if (!ready) return
      setCatalogue(owner.catalogue())
      setScriptProblems(await owner.loadMyScripts())
      setCatalogue(owner.catalogue())
    })
  }
  const openTimeframes = () => {
    setPinNotice(null)
    openSheet({ sheet: 'timeframe' })
  }
  const toggleDraw = () => {
    setInspect(null)
    if (drawing) owner?.drawings.cancel()
    setDrawing(!drawing)
  }
  /** Buy or Sell at the price under the held finger, where orders can be placed (C10). */
  const held = inspect?.price ?? null
  const heldOrder =
    held !== null && !ordersOff
      ? {
          price: formatPrice(held, dp(held)),
          onOrder: (orderSide: 'BUY' | 'SELL') => {
            setInspect(null)
            openTicket(orderSide, held)
          },
        }
      : null
  return (
    <Screen>
      {sideways ? (
        <SidewaysStrip
          symbol={symbol}
          prices={prices}
          prevClose={prevClose}
          status={status}
          dp={dp}
          timeframe={timeframe}
          studies={studies.length}
          style={style}
          orders={quoteOnly ? 'none' : info ? 'on' : 'off'}
          onBack={back}
          onTimeframe={openTimeframes}
          onIndicators={openIndicators}
          onStyle={() => openSheet({ sheet: 'style' })}
          onOrder={(orderSide) => openTicket(orderSide)}
          drawing={drawing}
          onDraw={toggleDraw}
        />
      ) : (
        <>
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
            openInterest={hasOpenInterest(exchange) ? { prev: prevOi } : null}
          />
          {!quoteOnly && (
            <DepthStrip
              prices={prices}
              decimals={dp(prices.get().tick?.ltp ?? 0)}
              quiet={bookQuiet(status)}
              onOpen={() => openSheet({ sheet: 'depth' })}
            />
          )}

          <TimeframeRow
            chips={chips}
            current={timeframe}
            onPick={pickChip}
            onMore={openTimeframes}
          />

          <div
            className="flex shrink-0 gap-1 border-b bg-card px-1 py-0.5"
            data-testid="chart-tools"
          >
            <button
              type="button"
              onClick={openIndicators}
              className="inline-flex h-12 min-w-12 items-center gap-1.5 rounded-full px-3 text-sm font-semibold hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChromeIcon id="indicators" size={18} className="shrink-0" />
              Indicators{' '}
              {studies.length > 0 && (
                <span className="rounded-full bg-[color:var(--primary-tint)] px-1.5 text-xs font-bold text-primary">
                  {studies.length} <span className="sr-only">on the chart</span>
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => openSheet({ sheet: 'style' })}
              aria-label={`Chart style: ${styleOf(style).label}`}
              className="inline-flex h-12 min-w-12 items-center gap-1.5 rounded-full px-3 text-sm font-semibold hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChromeIcon id={styleOf(style).glyph} size={18} className="shrink-0" />
              {styleOf(style).label}
              <ChevronDown aria-hidden="true" className="size-3.5 shrink-0" />
            </button>
            <button
              type="button"
              onClick={toggleDraw}
              aria-pressed={drawing}
              className={cn(
                'inline-flex h-12 min-w-12 items-center gap-1.5 rounded-full px-3 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                drawing ? 'bg-[color:var(--primary-tint)] text-primary' : 'hover:bg-accent'
              )}
            >
              {drawToolIcon('trend-line', 18, 'shrink-0')}
              Draw
            </button>
          </div>
        </>
      )}

      <div
        ref={surface}
        className="relative min-h-0 flex-1 touch-none bg-card"
        data-testid="chart-surface"
      >
        <div ref={host} className="absolute inset-0" />
        {owner && studies.length > 0 && (
          <StudyLegend
            owner={owner}
            prices={prices}
            heldTime={inspect?.bar.time ?? null}
            below={drawing ? DRAW_ROW : inspect ? (heldOrder ? READOUT_WITH_ORDERS : READOUT) : 0}
          />
        )}
        {owner && (
          <LineLabels
            owner={owner}
            lines={drawn.lines}
            more={drawn.more}
            bookAt={books.bookAt}
            prices={prices}
            quiet={priceQuiet(status)}
            onOpen={(ids) => openSheet({ sheet: 'line', ids })}
          />
        )}
        {owner && drawing && <DrawRow drawings={owner.drawings} onDone={toggleDraw} />}
        {owner && (
          <DrawingSheet
            drawings={owner.drawings}
            onEditNote={(id) => openSheet({ sheet: 'note', id })}
          />
        )}
        {inspect && (
          <Crosshair
            x={inspect.x}
            y={inspect.y}
            price={held === null ? null : formatPrice(held, dp(held))}
          />
        )}
        {inspect && (
          <Readout
            bar={inspect.bar}
            dp={dp(inspect.bar.close)}
            interval={timeframe}
            volume={!quoteOnly}
            order={heldOrder}
            onClose={() => setInspect(null)}
          />
        )}
        <LoadState
          view={load}
          onRetry={() => void owner?.load(symbol, exchange, timeframe)}
          onDismiss={() => setLoad(null)}
        />
      </div>

      {!sideways && (
        <footer className="shrink-0 border-t bg-card px-3 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
          <div className="flex min-h-12 items-center justify-between gap-3">
            {wake.supported ? (
              <label className="flex min-h-12 items-center gap-2 text-sm font-medium">
                <SunMedium aria-hidden="true" className="size-4 text-muted-foreground" />
                Keep screen on
                <Switch
                  checked={wake.on}
                  onCheckedChange={wake.setOn}
                  aria-describedby={wake.refused ? 'chart-wake-refused' : undefined}
                />
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
          {wake.refused && (
            <output
              id="chart-wake-refused"
              className="block pb-2 text-xs font-medium text-[color:var(--warning-text)]"
            >
              The phone would not keep the screen on. If battery saver is on, turn it off, then
              switch this on again.
            </output>
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={ordersOff}
              onClick={() => openTicket('BUY')}
              className="h-12 rounded-xl bg-[color:var(--buy-solid)] text-base font-bold tracking-[0.06em] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-45"
            >
              BUY
            </button>
            <button
              type="button"
              disabled={ordersOff}
              onClick={() => openTicket('SELL')}
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
      )}

      {/* Mounted while open only: closed, it would still draw with every change
          to the app's market data context. */}
      {info && side !== null && (
        <PlaceOrderDialog
          open
          onOpenChange={(open) => !open && closeSheet()}
          symbol={symbol}
          exchange={exchange}
          action={side}
          lotSize={info.lotSize}
          tickSize={info.tickSize}
          // The product picked for this segment on this phone (M8.2), MIS
          // until one is: the owner trades intraday.
          product={addTo ?? ticketProduct(exchange) ?? 'MIS'}
          // From a candle reading: a limit at the price held (C10); the product rule is the same.
          priceType={heldPrice === undefined ? undefined : 'LIMIT'}
          price={heldPrice}
          strategy={STRATEGY}
        />
      )}
      {owner && sheet?.sheet === 'note' && (
        <NoteSheet
          key={sheet.id}
          words={owner.drawings.noteOf(sheet.id)}
          onSave={(words) => {
            owner.drawings.setNote(sheet.id, words)
            closeSheet()
          }}
        />
      )}
      <LineSheet
        ids={sheet?.sheet === 'line' ? sheet.ids : null}
        lines={drawn.lines}
        books={books}
        apiKey={config?.apiKey ?? null}
        tick={info?.tickSize ?? null}
        canAdd={!ordersOff}
        prices={prices}
        quiet={priceQuiet(status)}
        onClose={closeSheet}
        onAdd={(position) =>
          openSheet({
            sheet: 'ticket',
            side: Number(position.quantity) < 0 ? 'SELL' : 'BUY',
            product: addProduct(position) ?? 'MIS',
          })
        }
        onEditAlert={(id) => openSheet({ sheet: 'alert', id })}
      />
      {sheet?.sheet === 'alert' && (
        <AlertFormSheet
          row={
            sheet.id === undefined
              ? null
              : (drawn.lines.flatMap((line) =>
                  line.ref.kind === 'alert' && line.ref.alert.id === sheet.id
                    ? [line.ref.alert]
                    : []
                )[0] ?? null)
          }
          prefill={{
            symbol,
            exchange,
            tick: info?.tickSize ?? null,
            price: prices.get().tick?.ltp ?? null,
          }}
          onClose={closeSheet}
          onSaved={(saved, created) => {
            closeSheet()
            toastAlertSet(saved, navigate, created)
          }}
        />
      )}
      <IndicatorSheet
        open={sheet?.sheet === 'indicators'}
        onOpenChange={(open) => !open && closeSheet()}
        set={studies}
        rows={(owner?.legendRows() ?? []).map((row) => ({
          name: `${row.title} ${row.params}`.trim(),
          note: row.note,
        }))}
        derivative={hasOpenInterest(exchange)}
        notice={studyNotice}
        catalogue={catalogue}
        problems={scriptProblems}
        onSettings={(index) =>
          // Over the indicator sheet, so Back returns to it.
          navigate(location.pathname + location.search, { state: { sheet: 'study', index } })
        }
        onAdd={addStudy}
        onRemove={(index) => {
          setStudyNotice(null)
          const next = studies.filter((_, i) => i !== index)
          setStudies(next)
          saveStudies(next)
        }}
      />
      <IndicatorSettingsSheet
        open={sheet?.sheet === 'study'}
        onOpenChange={(open) => !open && closeSheet()}
        form={sheet?.sheet === 'study' ? (owner?.studyForm(sheet.index) ?? null) : null}
        intervals={(groups ?? []).flatMap((g) =>
          g.items.map((item) => ({ label: intervalLabel(item), value: item }))
        )}
        onChange={(patch) => sheet?.sheet === 'study' && changeStudy(sheet.index, patch)}
        onDefaults={() => sheet?.sheet === 'study' && changeStudy(sheet.index, null)}
      />
      <DepthSheet
        open={sheet?.sheet === 'depth'}
        onOpenChange={(open) => !open && closeSheet()}
        prices={prices}
        decimals={dp(prices.get().tick?.ltp ?? 0)}
        quiet={bookQuiet(status)}
      />
      <StyleSheet
        open={sheet?.sheet === 'style'}
        onOpenChange={(open) => !open && closeSheet()}
        current={style}
        onPick={(next) => {
          setStyle(next)
          saveStyle(next)
          closeSheet()
        }}
      />
      <TimeframeSheet
        open={sheet?.sheet === 'timeframe'}
        onOpenChange={(open) => !open && closeSheet()}
        groups={groups}
        current={timeframe}
        pinned={chips}
        notice={pinNotice}
        onPick={(next) => {
          pickChip(next)
          closeSheet()
        }}
        onTogglePin={flipPin}
      />
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
})

/**
 * A text note's words (C7), asked for as soon as the note is placed. Saved
 * empty, or closed without words, the note is taken off the chart: a note with
 * nothing in it is nothing to keep.
 */
function NoteSheet({ words, onSave }: { words: string; onSave(words: string): void }) {
  const [text, setText] = useState(words)
  return (
    <BottomSheet
      open
      onOpenChange={(open) => !open && onSave(words)}
      title="Text note"
      description="Written on the chart where you tapped"
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          onSave(text)
        }}
      >
        <label
          htmlFor="chart-note"
          className="text-xs font-semibold text-[color:var(--text-secondary)]"
        >
          Note
        </label>
        <Input
          id="chart-note"
          value={text}
          maxLength={80}
          autoFocus
          onChange={(event) => setText(event.target.value)}
          className="h-12 text-base"
        />
        <button
          type="submit"
          className="h-12 rounded-xl bg-primary text-base font-semibold text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {text.trim() === '' ? 'Remove note' : 'Save note'}
        </button>
      </form>
    </BottomSheet>
  )
}

/**
 * The pinned timeframes and, last, More: every timeframe the broker offers,
 * in a sheet. A timeframe picked there that is not pinned takes More's place,
 * shown as chosen, so the row always says what the chart shows. Seven at
 * most, each 48 dp wide, fit a 360 px phone.
 */
function TimeframeRow({
  chips,
  current,
  onPick,
  onMore,
}: {
  chips: readonly string[]
  current: string
  onPick(interval: string): void
  onMore(): void
}) {
  const elsewhere = !chips.includes(current)
  const chip = (pressed: boolean) =>
    cn(
      'h-12 min-w-12 flex-1 rounded-full text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      pressed
        ? 'bg-[color:var(--primary-tint)] text-primary shadow-[inset_0_0_0_1.5px_var(--primary)]'
        : 'text-muted-foreground hover:bg-accent'
    )
  return (
    <fieldset className="flex shrink-0 gap-0.5 border-b bg-card px-1 py-1">
      <legend className="sr-only">Timeframe</legend>
      {chips.map((value) => (
        <button
          key={value}
          type="button"
          aria-pressed={value === current}
          onClick={() => onPick(value)}
          className={chip(value === current)}
        >
          {intervalLabel(value)}
        </button>
      ))}
      <button
        type="button"
        aria-pressed={elsewhere}
        aria-label={elsewhere ? `${intervalLabel(current)}, more timeframes` : 'More timeframes'}
        onClick={onMore}
        className={cn(chip(elsewhere), 'inline-flex items-center justify-center gap-0.5')}
      >
        {elsewhere ? intervalLabel(current) : 'More'}
        <ChevronDown aria-hidden="true" className="size-3 shrink-0" />
      </button>
    </fieldset>
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
  openInterest,
}: {
  prices: PriceStore
  prevClose: number | null
  status: LiveStatus | null
  dp(ltp: number): number
  /** An index is quoted, not traded. */
  traded: boolean
  /** The session's close, while the forming bar would run past it. */
  closesAt: number | null
  /** On a contract: its open interest, with the change since yesterday's close (C9). */
  openInterest: { prev: number | null } | null
}) {
  const { tick, streamed, oi } = useSyncExternalStore(prices.subscribe, prices.get)
  const now = useNow()
  const ltp = tick?.ltp ?? null
  const decimals = dp(ltp ?? 0)
  const { change, percent, direction } = changeOn(ltp, prevClose, decimals)
  const line = status?.line
  const quiet = priceQuiet(status)
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
      {openInterest && <OpenInterestLine oi={oi} prev={openInterest.prev} quiet={quiet} />}
    </section>
  )
}

/**
 * A contract's open interest (C9) and its change since yesterday's close,
 * with an arrow and a sign; said in words where either is not known yet.
 */
function OpenInterestLine({
  oi,
  prev,
  quiet,
}: {
  oi: number | null
  prev: number | null
  quiet: boolean
}) {
  const change = oi !== null && prev !== null ? oi - prev : null
  const direction = change === null || change === 0 ? 'flat' : change > 0 ? 'up' : 'down'
  const sign = change === null || change === 0 ? '' : change > 0 ? '+' : '−'
  return (
    <p
      className={cn(
        'mt-1 flex items-center gap-1.5 text-xs font-semibold',
        quiet && 'text-muted-foreground'
      )}
      data-testid="chart-oi"
    >
      <span className="text-[color:var(--text-tertiary)]">OI</span>
      {oi === null ? (
        <span className="font-medium text-muted-foreground">waiting for the first tick</span>
      ) : (
        <>
          <span className="tabular-nums">{compact.format(oi)}</span>
          {change === null ? (
            <span className="font-medium text-muted-foreground">change not available</span>
          ) : (
            <span
              className={cn('inline-flex items-center gap-1 tabular-nums', TREND_TEXT[direction])}
            >
              <TrendArrow direction={direction} className="size-2" />
              {sign}
              {compact.format(Math.abs(change))}
              <span className="font-medium text-[color:var(--text-tertiary)]">since yesterday</span>
            </span>
          )}
        </>
      )}
    </p>
  )
}

/** Why the book is not current, in words, or null while it is (C9). */
function bookQuiet(status: LiveStatus | null): string | null {
  const line = status?.line
  if (!line) return null
  if (line.kind === 'closed') return 'Market closed: the book as the session left it.'
  if (line.kind === 'connecting') return 'Reconnecting: the book is not current.'
  if (line.kind === 'polling' || (line.kind === 'delayed' && line.stale))
    return 'Delayed: the book is not current.'
  return null
}

/**
 * Hair lines through the candle being read and the finger's height, with the
 * price at that height on the price axis, where the candles' pane has one (C10).
 */
function Crosshair({ x, y, price }: { x: number | null; y: number; price: string | null }) {
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
      {price !== null && (
        <span
          className="absolute right-0 -translate-y-1/2 rounded-sm bg-[color:var(--text-primary)] px-1 py-0.5 text-[0.6875rem] leading-none font-semibold text-white tabular-nums"
          style={{ top: y }}
          data-testid="chart-held-price"
        >
          {price}
        </span>
      )}
    </div>
  )
}

/** The reading's height, which the study legend moves below: with Buy and Sell at the held price, more. */
const READOUT = 88
const READOUT_WITH_ORDERS = READOUT + 52

/**
 * The candle under a held finger: when, O H L C, and V where it trades; and,
 * where orders can be placed, Buy and Sell at the price at the finger's
 * height, each opening the order ticket with that price as a limit (C10).
 *
 * It appears under the finger that held, and a finger lifted soon after the
 * hold is a tap to the browser, landing on whatever is there now: for its
 * first half second the reading takes no tap (`useOpenGuard`), as a sheet does.
 */
function Readout({
  bar,
  dp,
  interval,
  volume,
  order,
  onClose,
}: {
  bar: Bar
  dp: number
  interval: string
  volume: boolean
  /** The held price, written at the instrument's decimals; null where no order can be placed. */
  order: { price: string; onOrder(side: 'BUY' | 'SELL'): void } | null
  onClose(): void
}) {
  const at = new Date(bar.time * 1000)
  const stamp = isCalendarInterval(interval) ? barDay.format(at) : barStamp.format(at)
  const cells: Array<[string, string]> = [
    ['O', formatPrice(bar.open, dp)],
    ['H', formatPrice(bar.high, dp)],
    ['L', formatPrice(bar.low, dp)],
    ['C', formatPrice(bar.close, dp)],
  ]
  if (volume) cells.push(['V', typeof bar.volume === 'number' ? compact.format(bar.volume) : '—'])
  const guard = useOpenGuard(true)
  return (
    <div
      // On a wide or sideways screen, a card at the left that leaves the latest candles in view.
      className="absolute top-2 right-2 left-2 z-10 rounded-xl border bg-card py-1 pr-1 pl-3 shadow-[var(--shadow-raised)] sm:right-auto sm:w-[28rem]"
      data-testid="chart-readout"
      // A touch on the reading is its own, not the chart's (longPress OVER_CHART): its taps land.
      data-over-chart=""
      data-settling={guard.settling || undefined}
      onPointerDownCapture={guard.handlers.onPointerDownCapture}
      onClickCapture={guard.handlers.onClickCapture}
      onKeyDownCapture={guard.handlers.onKeyDownCapture}
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
      {order && (
        <div className="grid grid-cols-2 gap-2 pr-2 pb-1">
          <button
            type="button"
            onClick={() => order.onOrder('BUY')}
            className="h-12 rounded-xl border-2 border-[color:var(--buy-solid)] px-2 text-sm font-bold text-[color:var(--buy-solid)] tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Buy at {order.price}
          </button>
          <button
            type="button"
            onClick={() => order.onOrder('SELL')}
            className="h-12 rounded-xl border-2 border-[color:var(--sell-solid)] px-2 text-sm font-bold text-[color:var(--sell-solid)] tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Sell at {order.price}
          </button>
        </div>
      )}
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
