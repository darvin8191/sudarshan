import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import {
  type InitialEntry,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartStateView } from '@/lib/trading/chartState'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import { useAuthStore } from '@/stores/authStore'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import { rememberProduct } from '@/sudarshan/ticket/productPref'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { PhoneChartCallbacks, PhoneTick } from './phoneChart'
import { SIDEWAYS_QUERY } from './usePhoneLayout'

const fakes = vi.hoisted(() => ({
  charts: [] as Array<{
    opts: { callbacks: PhoneChartCallbacks; appMode: string; style?: string }
    load: ReturnType<typeof vi.fn>
    setAppMode: ReturnType<typeof vi.fn>
    setStyle: ReturnType<typeof vi.fn>
    setStudies: ReturnType<typeof vi.fn>
    setTick: ReturnType<typeof vi.fn>
    legendRows: ReturnType<typeof vi.fn>
    destroy: ReturnType<typeof vi.fn>
    barAt: ReturnType<typeof vi.fn>
    barX: ReturnType<typeof vi.fn>
    priceAt: ReturnType<typeof vi.fn>
    setOverlays: ReturnType<typeof vi.fn>
    moveLines(ys: Array<{ id: string; y: number }>): void
  }>,
  /** The books the chart's lines are read from (C5). */
  positions: [] as unknown[],
  orders: [] as unknown[],
  alerts: [] as unknown[],
  config: { apiKey: 'test-key', wsUrl: 'ws://test.invalid' } as unknown,
  instrument: { name: 'STATE BANK OF INDIA', lotSize: 1, tickSize: 0.05 } as unknown,
  /** What the broker offers, as the terminal groups it (Upstox's set). */
  intervals: null as unknown,
  /** Every study on offer, and the owner's files that failed, as the host gives them (C4). */
  catalogue: [] as Array<{ id: string; name: string; category: string; mine: boolean }>,
  problems: [] as string[],
  /** A contract's open interest at yesterday's close (C9). */
  prevOi: null as number | null,
  /** The price at a held finger's height, as the host gives it (C10). */
  heldPrice: 814.35 as number | null,
}))

const UPSTOX_INTERVALS = [
  { label: 'minutes', items: ['1m', '2m', '3m', '5m', '10m', '15m', '30m'] },
  { label: 'hours', items: ['1h', '2h', '3h', '4h'] },
  { label: 'days', items: ['D'] },
  { label: 'weeks', items: ['W'] },
  { label: 'months', items: ['M'] },
]

vi.mock('./phoneChart', () => ({
  isQuoteOnly: (exchange: string) => exchange.endsWith('_INDEX'),
  PhoneChart: class {
    opts: unknown
    load = vi.fn(async () => {})
    setAppMode = vi.fn()
    setStyle = vi.fn(async () => {})
    setStudies = vi.fn(async (_set: Array<{ id: string }>) => {})
    setTick = vi.fn()
    loadIndicatorTier = vi.fn(async () => true)
    loadMyScripts = vi.fn(async () => fakes.problems)
    catalogue = vi.fn(() => fakes.catalogue)
    /** EMA's form, its values over the defaults as the page last set them. */
    studyForm = vi.fn((index: number) => {
      const entry = this.setStudies.mock.lastCall?.[0]?.[index] as
        | { id: string; settings: Record<string, unknown> }
        | undefined
      if (!entry) return null
      return {
        name: 'EMA',
        fields: [
          { key: 'length', type: 'number', label: 'Length', min: 1, max: 500, step: 1 },
          {
            key: 'source',
            type: 'select',
            label: 'Source',
            options: [
              { label: 'Close', value: 'close' },
              { label: 'Open', value: 'open' },
            ],
          },
        ],
        values: { length: 9, source: 'close', ...entry.settings },
      }
    })
    studyFacts = vi.fn((id: string) =>
      id === 'rsi' || id === 'macd' || id === 'atr'
        ? { name: id.toUpperCase(), placement: 'pane' }
        : null
    )
    /** One line per study the page last set, as the host gives them. */
    legendRows = vi.fn(() =>
      (this.setStudies.mock.lastCall?.[0] ?? []).map((entry: { id: string }, i: number) => ({
        key: `${i}:${entry.id}`,
        title: entry.id.toUpperCase(),
        params: '',
        values: ['1.00'],
        pane: 0,
        top: 0,
      }))
    )
    destroy = vi.fn()
    barAt = vi.fn(() => ({
      time: Date.UTC(2026, 9, 20, 4, 45) / 1000,
      open: 812.5,
      high: 815.25,
      low: 811,
      close: 814.1,
      volume: 1_234_567,
    }))
    barX = vi.fn(() => 120)
    priceAt = vi.fn(() => fakes.heldPrice)
    /** The lines the page laid over the chart (C5), and where the host says each stands. */
    setOverlays = vi.fn()
    private ys: Array<{ id: string; y: number }> = []
    private readonly heard = new Set<() => void>()
    overlayLayout = () => this.ys
    onOverlayLayout = (listener: () => void) => {
      this.heard.add(listener)
      return () => this.heard.delete(listener)
    }
    moveLines(ys: Array<{ id: string; y: number }>) {
      this.ys = ys
      for (const listener of this.heard) listener()
    }
    constructor(opts: unknown) {
      this.opts = opts
      fakes.charts.push(this as never)
    }
  },
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getPositions: vi.fn(async () => ({ status: 'success', data: fakes.positions })),
    getOrders: vi.fn(async () => ({
      status: 'success',
      data: { orders: fakes.orders, statistics: {} },
    })),
  },
}))
vi.mock('./api', () => ({
  loadFeedConfig: async () => fakes.config,
  loadInstrument: async () => fakes.instrument,
  loadIntervals: async () => fakes.intervals,
  loadPreviousOi: async () => fakes.prevOi,
  searchSymbols: async () => [],
}))
// Both close themselves the way the real dialogs do: the action's own
// callback first, then onOpenChange(false).
vi.mock('@/components/trading/PlaceOrderDialog', () => ({
  PlaceOrderDialog: (props: {
    open: boolean
    onOpenChange(open: boolean): void
    onSuccess?(orderId: string): void
    action: string
    product: string
    lotSize: number
    tickSize: number
    strategy: string
    symbol: string
    exchange: string
    priceType?: string
    price?: number
  }) =>
    props.open ? (
      <div role="dialog" aria-label="Order ticket">
        {[
          props.action,
          props.symbol,
          props.exchange,
          props.product,
          props.lotSize,
          props.strategy,
          props.priceType ?? 'MARKET',
          props.price ?? 'no price',
        ]
          .map(String)
          .join(' ')}
        <button
          type="button"
          className="h-12"
          onClick={() => {
            props.onSuccess?.('1')
            props.onOpenChange(false)
          }}
        >
          Place order
        </button>
      </div>
    ) : null,
}))
vi.mock('@/components/trading/SymbolSearchDialog', () => ({
  SymbolSearchDialog: (props: {
    open: boolean
    onOpenChange(open: boolean): void
    onPick(row: { symbol: string; exchange: string }): void
  }) =>
    props.open ? (
      <div role="dialog" aria-label="Symbol Search">
        <button
          type="button"
          className="h-12"
          onClick={() => {
            props.onPick({ symbol: 'RELIANCE', exchange: 'NSE' })
            props.onOpenChange(false)
          }}
        >
          RELIANCE
        </button>
      </div>
    ) : null,
}))

// SUDARSHAN M10.2: the market alert form the bell opens: its own price stream and its save.
vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const alerting = vi.hoisted(() => ({
  create: vi.fn(),
  prefs: vi.fn(async () => ({ telegram: true, app: true })),
  toasts: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: {
    create: alerting.create,
    prefs: alerting.prefs,
    state: async () => ({ enabled: true, alerts: fakes.alerts }),
  },
}))
vi.mock('@/utils/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/toast')>()),
  toast: alerting.toasts,
}))

import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/providers'
import MarketDataContext from '@/contexts/MarketDataContext'
import { MarketDataManager } from '@/lib/MarketDataManager'
import ChartPage from './ChartPage'

function Where() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname + location.search}</output>
}

/** The phone's own Back, which a page cannot see coming. */
function PhoneBack() {
  const navigate = useNavigate()
  return (
    <button type="button" className="h-12" onClick={() => navigate(-1)}>
      Phone back
    </button>
  )
}

function renderAt(path: InitialEntry, before: InitialEntry[] = []) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[...before, path]}>
        <Routes>
          <Route path="/chart" element={<ChartPage />} />
          <Route path="/chart/:exchange/:symbol" element={<ChartPage />} />
          <Route path="*" element={<p>Elsewhere</p>} />
        </Routes>
        <Where />
        <PhoneBack />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const chart = () => {
  const latest = fakes.charts.at(-1)
  if (!latest) throw new Error('no chart was built')
  return latest
}

const status = (line: FeedLine, opensAt: number | null = null): LiveStatus => ({
  line,
  lastTickAt: null,
  lastUpdateAt: null,
  lastTickTime: null,
  staleAfterMs: 30_000,
  opensAt,
})

function emit(fn: (callbacks: PhoneChartCallbacks) => void) {
  act(() => fn(chart().opts.callbacks))
}

/** A finger held on the chart until the candle under it is read. */
async function hold() {
  const down = new MouseEvent('pointerdown', { bubbles: true, clientX: 120, clientY: 200 })
  Object.assign(down, { pointerId: 1, pointerType: 'touch' })
  fireEvent(screen.getByTestId('chart-surface'), down)
  const readout = await screen.findByTestId('chart-readout', {}, { timeout: 2000 })
  fireEvent(
    readout,
    Object.assign(new MouseEvent('pointerup', { bubbles: true }), {
      pointerId: 1,
      pointerType: 'touch',
    })
  )
  return readout
}

/** A finger's touch on a control, as a phone sends it before the tap. */
function touch(element: Element) {
  const down = new MouseEvent('pointerdown', { bubbles: true })
  Object.assign(down, { pointerId: 2, pointerType: 'touch' })
  fireEvent(element, down)
}

/** The phone, upright or on its side, as the page's media query hears it; turned on demand. */
function phoneHeld(initial: 'upright' | 'sideways') {
  let way = initial
  const listeners = new Set<() => void>()
  const spy = vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        get matches() {
          return way === 'sideways' && query === SIDEWAYS_QUERY
        },
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList
  )
  return {
    spy,
    turn(next: 'upright' | 'sideways') {
      way = next
      act(() => {
        for (const listener of [...listeners]) listener()
      })
    },
  }
}

async function opened(path: InitialEntry = '/chart/NSE/SBIN', before: InitialEntry[] = []) {
  const view = renderAt(path, before)
  await waitFor(() => expect(fakes.charts.length).toBeGreaterThan(0))
  await waitFor(() => expect(chart().load).toHaveBeenCalled())
  return view
}

beforeEach(() => {
  fakes.charts.length = 0
  fakes.config = { apiKey: 'test-key', wsUrl: 'ws://test.invalid' }
  useAuthStore.setState({ apiKey: 'test-key', sessionChecked: true, brokerSessionExpired: false })
  fakes.instrument = { name: 'STATE BANK OF INDIA', lotSize: 1, tickSize: 0.05 }
  fakes.intervals = UPSTOX_INTERVALS
  fakes.catalogue = [
    { id: 'ichimoku', name: 'Ichimoku Cloud', category: 'Trend', mine: false },
    { id: 'stochastic', name: 'Stochastic', category: 'Momentum', mine: false },
  ]
  fakes.problems = []
  fakes.prevOi = null
  fakes.heldPrice = 814.35
  fakes.positions = []
  fakes.orders = []
  fakes.alerts = []
  localStorage.clear()
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 })
})

afterEach(() => {
  Reflect.deleteProperty(navigator, 'wakeLock')
})

describe('the phone chart page at 390 px', () => {
  it('names the instrument and loads it at the timeframe last chosen', async () => {
    localStorage.setItem('sudarshan-chart-interval', '15m')
    await opened()
    expect(screen.getByRole('button', { name: 'SBIN on NSE. Change instrument' })).toBeVisible()
    await screen.findByText('NSE · STATE BANK OF INDIA')
    expect(chart().load).toHaveBeenCalledWith('SBIN', 'NSE', '15m')
    expect(screen.getByRole('button', { name: '15m' })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('sudarshan-chart-last')).toBe('{"symbol":"SBIN","exchange":"NSE"}')
  })

  it('shows the price, its change with sign and arrow, and the last tick', async () => {
    await opened()
    expect(screen.getByTestId('chart-ltp')).toHaveTextContent('No price yet')
    const tick: PhoneTick = {
      ltp: 1234.5,
      time: Date.UTC(2026, 9, 20, 4, 45, 32) / 1000,
      tradeTime: null,
      at: Date.now(),
      polled: false,
    }
    emit((cb) => {
      cb.onTick(tick)
      cb.onPrevClose?.(1200)
      cb.onStatus(status({ kind: 'live' }))
    })
    expect(screen.getByTestId('chart-ltp')).toHaveTextContent('1,234.50')
    const change = screen.getByTestId('chart-change')
    expect(change).toHaveTextContent('+₹34.50 (+2.88%)')
    expect(change.querySelector('[data-direction="up"]')).not.toBeNull()
    expect(screen.getByTestId('chart-last-tick')).toHaveTextContent('Last tick 10:15:32')
    expect(screen.getByText('Real-time')).toBeVisible()
  })

  it('writes a fall with a minus sign and a down arrow, not colour alone', async () => {
    await opened()
    emit((cb) => {
      cb.onTick({ ltp: 98, time: null, tradeTime: null, at: Date.now(), polled: false })
      cb.onPrevClose?.(100)
    })
    const change = screen.getByTestId('chart-change')
    expect(change).toHaveTextContent('−₹2.00 (−2.00%)')
    expect(change.querySelector('[data-direction="down"]')).not.toBeNull()
  })

  it.each<[string, FeedLine, RegExp]>([
    ['Real-time', { kind: 'live' }, /^Real-time$/],
    [
      'Delayed',
      { kind: 'polling', at: Date.now() - 3000, stale: false },
      /^Delayed · updated \ds ago$/,
    ],
    [
      'Stale',
      { kind: 'delayed', at: Date.now() - 40_000, stale: true },
      /^Stale · updated \d+s ago$/,
    ],
    [
      'Reconnecting',
      { kind: 'connecting', at: Date.now() - 5000, stale: false, again: true },
      /^Reconnecting · updated \ds ago$/,
    ],
    ['Market closed', { kind: 'closed' }, /^Market closed$/],
  ])('says %s with a word and a mark, never colour alone', async (_name, line, words) => {
    await opened()
    emit((cb) => cb.onStatus(status(line)))
    const chip = document.querySelector('[data-feed]')
    expect(chip?.textContent).toMatch(words)
    expect(chip?.querySelector('svg, [aria-hidden="true"]')).not.toBeNull()
  })

  it('greys the price while it is not real-time and should be', async () => {
    await opened()
    emit((cb) => {
      cb.onTick({ ltp: 100, time: null, tradeTime: null, at: Date.now(), polled: true })
      cb.onStatus(status({ kind: 'connecting', at: Date.now(), stale: false, again: true }))
    })
    expect(screen.getByTestId('chart-ltp')).toHaveClass('text-muted-foreground')
    emit((cb) => cb.onStatus(status({ kind: 'live' })))
    expect(screen.getByTestId('chart-ltp')).not.toHaveClass('text-muted-foreground')
  })

  it('says when a closed market opens', async () => {
    await opened()
    const opens = Date.UTC(2026, 9, 22, 3, 45) / 1000
    emit((cb) => cb.onStatus(status({ kind: 'closed' }, opens)))
    expect(screen.getByTestId('chart-last-tick')).toHaveTextContent(/^Opens (at 09:15|Thu 09:15)$/)
  })

  it('switches timeframe and remembers it on this phone', async () => {
    await opened()
    expect(screen.getByRole('button', { name: '5m' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: '1h' }))
    expect(chart().load).toHaveBeenLastCalledWith('SBIN', 'NSE', '1h')
    expect(screen.getByRole('button', { name: '1h' })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('sudarshan-chart-interval')).toBe('1h')
  })

  it('offers every timeframe the broker has from More, and a pick off the chips takes its place', async () => {
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'More timeframes' }))
    const sheet = await screen.findByRole('dialog', { name: 'Timeframe' })
    for (const heading of ['Minutes', 'Hours', 'Days', 'Weeks', 'Months'])
      expect(within(sheet).getByRole('heading', { name: heading })).toBeVisible()
    expect(within(sheet).getByRole('button', { name: '1M, 1 month' })).toBeVisible()
    await userEvent.click(within(sheet).getByRole('button', { name: '2h, 2 hours' }))
    expect(chart().load).toHaveBeenLastCalledWith('SBIN', 'NSE', '2h')
    expect(localStorage.getItem('sudarshan-chart-interval')).toBe('2h')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Timeframe' })).toBeNull())
    // Not pinned, so it shows in More's place, as the timeframe in use.
    const more = screen.getByRole('button', { name: '2h, more timeframes' })
    expect(more).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '5m' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('pins up to six chips, says why a seventh is refused, and keeps the pins on this phone', async () => {
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'More timeframes' }))
    const sheet = await screen.findByRole('dialog', { name: 'Timeframe' })
    const pin = (name: string) => within(sheet).getByRole('button', { name: `Pin ${name}` })
    expect(pin('3 minutes')).toHaveAttribute('aria-pressed', 'true')
    expect(pin('3 minutes')).toHaveTextContent('Pinned')
    await userEvent.click(pin('2 hours'))
    expect(within(sheet).getByRole('status')).toHaveTextContent(
      'Six timeframes are pinned. Unpin one to pin another.'
    )
    expect(pin('2 hours')).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(pin('3 minutes'))
    await userEvent.click(pin('2 hours'))
    expect(within(sheet).queryByRole('status')).toBeNull()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Timeframe' })).toBeNull())
    expect(JSON.parse(localStorage.getItem('sudarshan-chart-pinned') ?? '[]')).toEqual([
      '1m',
      '5m',
      '15m',
      '1h',
      '2h',
      'D',
    ])
    const row = screen.getByRole('group', { name: 'Timeframe' })
    expect(
      within(row)
        .getAllByRole('button')
        .map((b) => b.textContent)
    ).toEqual(['1m', '5m', '15m', '1h', '2h', '1D', 'More'])
  })

  it('keeps one chip pinned, and shows only chips the broker offers', async () => {
    localStorage.setItem('sudarshan-chart-pinned', JSON.stringify(['5m', '45m']))
    fakes.intervals = [{ label: 'minutes', items: ['1m', '5m', '15m'] }]
    await opened()
    const row = screen.getByRole('group', { name: 'Timeframe' })
    await waitFor(() =>
      expect(
        within(row)
          .getAllByRole('button')
          .map((b) => b.textContent)
      ).toEqual(['5m', 'More'])
    )
    await userEvent.click(screen.getByRole('button', { name: 'More timeframes' }))
    const sheet = await screen.findByRole('dialog', { name: 'Timeframe' })
    await userEvent.click(within(sheet).getByRole('button', { name: 'Pin 5 minutes' }))
    expect(within(sheet).getByRole('status')).toHaveTextContent(
      'One timeframe stays pinned above the chart.'
    )
  })

  it('moves off a timeframe this broker does not offer, to one it does', async () => {
    localStorage.setItem('sudarshan-chart-interval', '45m')
    await opened()
    await waitFor(() => expect(chart().load).toHaveBeenLastCalledWith('SBIN', 'NSE', '5m'))
  })

  it('reads a day chart with its old saved name, and stamps its candles with the date', async () => {
    localStorage.setItem('sudarshan-chart-interval', '1D')
    await opened()
    expect(chart().load).toHaveBeenCalledWith('SBIN', 'NSE', 'D')
    expect(screen.getByRole('button', { name: '1D' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('changes the chart style from its sheet, for every instrument, and says which is in use', async () => {
    await opened()
    expect(chart().opts.style).toBe('candlestick')
    await userEvent.click(screen.getByRole('button', { name: 'Chart style: Candles' }))
    const sheet = await screen.findByRole('dialog', { name: 'Chart style' })
    expect(within(sheet).getByRole('button', { name: 'Candles' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    await userEvent.click(within(sheet).getByRole('button', { name: 'Heikin Ashi' }))
    expect(chart().setStyle).toHaveBeenLastCalledWith('heikin-ashi')
    expect(localStorage.getItem('sudarshan-chart-style')).toBe('heikin-ashi')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chart style' })).toBeNull())
    expect(screen.getByRole('button', { name: 'Chart style: Heikin Ashi' })).toBeVisible()
  })

  it('builds the chart in the style chosen before', async () => {
    localStorage.setItem('sudarshan-chart-style', 'bar')
    await opened()
    expect(chart().opts.style).toBe('bar')
    expect(screen.getByRole('button', { name: 'Chart style: Bars' })).toBeVisible()
  })

  it('adds indicators from the common list, for every instrument, and draws a legend line for each', async () => {
    await opened()
    expect(chart().setStudies).toHaveBeenLastCalledWith([])
    await userEvent.click(screen.getByRole('button', { name: 'Indicators' }))
    const sheet = await screen.findByRole('dialog', { name: 'Indicators' })
    expect(within(sheet).getByText('No indicators yet. Add one below.')).toBeVisible()
    // Open interest is a contract's; a share does not offer it.
    expect(within(sheet).queryByRole('button', { name: 'Add Open interest' })).toBeNull()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add EMA' }))
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add RSI' }))
    expect(chart().setStudies).toHaveBeenLastCalledWith([
      { id: 'ema', settings: {} },
      { id: 'rsi', settings: {} },
    ])
    expect(JSON.parse(localStorage.getItem('sudarshan-chart-studies') ?? '[]')).toEqual([
      { id: 'ema', settings: {} },
      { id: 'rsi', settings: {} },
    ])
    expect(within(sheet).getByRole('button', { name: 'Remove EMA' })).toBeVisible()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Indicators' })).toBeNull())
    expect(screen.getByRole('button', { name: 'Indicators 2 on the chart' })).toBeVisible()
    expect(screen.getByTestId('study-legend')).toHaveTextContent('EMA1.00')
  })

  it('refuses a fourth indicator and a third pane, each with a sentence, and removes one', async () => {
    localStorage.setItem(
      'sudarshan-chart-studies',
      JSON.stringify(['ema', 'rsi', 'macd'].map((id) => ({ id, settings: {} })))
    )
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'Indicators 3 on the chart' }))
    const sheet = await screen.findByRole('dialog', { name: 'Indicators' })
    expect(sheet).toHaveAccessibleDescription('For every instrument on this phone · 3 of 3')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add Supertrend' }))
    expect(within(sheet).getByRole('status')).toHaveTextContent(
      'Three indicators at most on the phone chart. Remove one, or open the full terminal.'
    )
    await userEvent.click(within(sheet).getByRole('button', { name: 'Remove EMA' }))
    expect(within(sheet).queryByRole('status')).toBeNull()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add ATR' }))
    expect(within(sheet).getByRole('status')).toHaveTextContent(
      'Two indicators with a pane of their own at most on the phone chart.'
    )
    expect(chart().setStudies).toHaveBeenLastCalledWith(
      ['rsi', 'macd'].map((id) => ({ id, settings: {} }))
    )
  })

  it('offers open interest on a contract, and gives the chart its tick', async () => {
    fakes.instrument = { name: 'NIFTY 27 OCT 25900 CE', lotSize: 65, tickSize: 0.05 }
    await opened('/chart/NFO/NIFTY27OCT2625900CE')
    await waitFor(() => expect(chart().setTick).toHaveBeenCalledWith(0.05))
    await userEvent.click(screen.getByRole('button', { name: 'Indicators' }))
    const sheet = await screen.findByRole('dialog', { name: 'Indicators' })
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add Open interest' }))
    expect(chart().setStudies).toHaveBeenLastCalledWith([{ id: 'open-interest', settings: {} }])
  })

  it('changes a study in its own settings sheet, says what is wrong with a number, and Back returns to the list', async () => {
    localStorage.setItem(
      'sudarshan-chart-studies',
      JSON.stringify([{ id: 'ema', settings: { length: 21 } }])
    )
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'Indicators 1 on the chart' }))
    const list = await screen.findByRole('dialog', { name: 'Indicators' })
    await userEvent.click(within(list).getByRole('button', { name: 'Settings for EMA' }))
    const form = await screen.findByRole('dialog', { name: 'EMA' })
    const length = within(form).getByRole('textbox', { name: 'Length' })
    expect(length).toHaveValue('21')
    expect(length).toHaveAttribute('inputmode', 'decimal')
    await userEvent.clear(length)
    await userEvent.type(length, '0{Enter}')
    expect(within(form).getByText('The lowest allowed is 1')).toBeVisible()
    expect(chart().setStudies).toHaveBeenLastCalledWith([{ id: 'ema', settings: { length: 21 } }])
    await userEvent.clear(length)
    await userEvent.type(length, '50{Enter}')
    expect(chart().setStudies).toHaveBeenLastCalledWith([{ id: 'ema', settings: { length: 50 } }])
    expect(JSON.parse(localStorage.getItem('sudarshan-chart-studies') ?? '[]')).toEqual([
      { id: 'ema', settings: { length: 50 } },
    ])
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Source' }), 'open')
    expect(chart().setStudies).toHaveBeenLastCalledWith([
      { id: 'ema', settings: { length: 50, source: 'open' } },
    ])
    await userEvent.click(within(form).getByRole('button', { name: 'Back to defaults' }))
    expect(chart().setStudies).toHaveBeenLastCalledWith([{ id: 'ema', settings: {} }])
    // The phone's own Back, which reaches the page past the open sheet.
    fireEvent.click(screen.getByText('Phone back'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'EMA' })).toBeNull())
    expect(await screen.findByRole('dialog', { name: 'Indicators' })).toBeVisible()
  })

  it("finds any built-in by name, and lists the owner's own scripts with what failed", async () => {
    fakes.catalogue = [
      ...fakes.catalogue,
      { id: 'my-band', name: 'My Band', category: 'Custom', mine: true },
    ]
    fakes.problems = ['broken.js: it does not register anything']
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'Indicators' }))
    const sheet = await screen.findByRole('dialog', { name: 'Indicators' })
    const mine = await within(sheet).findByRole('region', { name: 'My scripts' })
    expect(within(mine).getByText('broken.js: it does not register anything')).toBeVisible()
    await userEvent.click(within(mine).getByRole('button', { name: 'Add My Band' }))
    await userEvent.type(within(sheet).getByRole('searchbox'), 'ichi')
    await userEvent.click(within(sheet).getByRole('button', { name: 'Add Ichimoku Cloud' }))
    expect(chart().setStudies).toHaveBeenLastCalledWith([
      { id: 'my-band', settings: {} },
      { id: 'ichimoku', settings: {} },
    ])
    await userEvent.clear(within(sheet).getByRole('searchbox'))
    await userEvent.type(within(sheet).getByRole('searchbox'), 'zzz')
    expect(within(sheet).getByText('No indicator by that name.')).toBeVisible()
  })

  it('shows the best bid and ask with their quantities, tick by tick, and five levels on a tap', async () => {
    await opened()
    const book = (bid: number) => ({
      bids: [100, 99.95, 99.9, 99.85, 99.8].map((p, i) => ({
        price: p + bid - 100,
        qty: 650 * (i + 1),
      })),
      asks: [100.05, 100.1, 100.15, 100.2, 0].map((p) => ({
        price: p ? p + bid - 100 : 0,
        qty: p ? 1_300 : 0,
      })),
      ltp: bid,
    })
    emit((cb) => {
      cb.onDepth?.(book(100))
      cb.onStatus(status({ kind: 'live' }))
    })
    await waitFor(() =>
      expect(screen.getByTestId('depth-strip')).toHaveTextContent(
        'Bid100.00 × 650Ask100.05 × 1,300'
      )
    )
    await userEvent.click(
      screen.getByRole('button', {
        name: /^Market depth: bid 100\.00, ask 100\.05\. Show five levels$/,
      })
    )
    const sheet = await screen.findByRole('dialog', { name: 'Market depth' })
    const rows = within(sheet).getAllByRole('row')
    expect(rows).toHaveLength(6)
    expect(rows[1]).toHaveTextContent('650100.00100.051,300')
    // An empty level the proxy pads with zeros is a dash, not a price of nothing.
    expect(rows[5]).toHaveTextContent('3,25099.80——')
  })

  it('greys the book and says why while prices are not real-time', async () => {
    await opened()
    emit((cb) => {
      cb.onDepth?.({
        bids: [{ price: 100, qty: 650 }],
        asks: [{ price: 100.05, qty: 1_300 }],
        ltp: 100,
      })
      cb.onStatus(status({ kind: 'connecting', at: Date.now(), stale: false, again: true }))
    })
    const strip = await screen.findByRole('button', {
      name: /Reconnecting: the book is not current/,
    })
    expect(strip).toHaveClass('text-muted-foreground')
  })

  it("shows a contract's open interest and its change since yesterday, with a sign and an arrow", async () => {
    fakes.instrument = { name: 'NIFTY 27 OCT 25900 CE', lotSize: 65, tickSize: 0.05 }
    fakes.prevOi = 1_150_000
    await opened('/chart/NFO/NIFTY27OCT2625900CE')
    const line = screen.getByTestId('chart-oi')
    expect(line).toHaveTextContent('OIwaiting for the first tick')
    emit((cb) =>
      cb.onTick({
        ltp: 142.3,
        time: null,
        tradeTime: null,
        at: Date.now(),
        polled: false,
        oi: 1_206_500,
      })
    )
    await waitFor(() => expect(line).toHaveTextContent('OI12.07L+56.5Ksince yesterday'))
    expect(line.querySelector('[data-direction="up"]')).not.toBeNull()
  })

  it("says the change is not available where yesterday's open interest is not known, and shows none for a share", async () => {
    fakes.instrument = { name: 'NIFTY 27 OCT 25900 CE', lotSize: 65, tickSize: 0.05 }
    await opened('/chart/NFO/NIFTY27OCT2625900CE')
    emit((cb) =>
      cb.onTick({
        ltp: 142.3,
        time: null,
        tradeTime: null,
        at: Date.now(),
        polled: false,
        oi: 1_206_500,
      })
    )
    await waitFor(() =>
      expect(screen.getByTestId('chart-oi')).toHaveTextContent('OI12.07Lchange not available')
    )
  })

  it('has no book strip or open interest for an index', async () => {
    await opened('/chart/NSE_INDEX/NIFTY')
    expect(screen.queryByTestId('depth-strip')).toBeNull()
    expect(screen.queryByTestId('chart-oi')).toBeNull()
  })

  it("opens the app's own order ticket from Buy and Sell, on Delivery for a share until a pick", async () => {
    await opened()
    const buy = await screen.findByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    await userEvent.click(buy)
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE CNC 1 chart-trading'
    )
  })

  it('opens the ticket on the product picked for the segment on this phone (M8.2)', async () => {
    rememberProduct('NSE', 'CNC')
    rememberProduct('NFO', 'NRML')
    await opened()
    const sell = await screen.findByRole('button', { name: 'SELL' })
    await waitFor(() => expect(sell).toBeEnabled())
    await userEvent.click(sell)
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'SELL SBIN NSE CNC 1 chart-trading'
    )
  })

  it("keeps another segment's pick out of the ticket", async () => {
    rememberProduct('NFO', 'NRML')
    await opened()
    const buy = await screen.findByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    await userEvent.click(buy)
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE CNC 1 chart-trading'
    )
  })

  it('keeps Buy and Sell off for an index, and says why', async () => {
    await opened('/chart/NSE_INDEX/NIFTY')
    expect(screen.getByRole('button', { name: 'BUY' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'SELL' })).toBeDisabled()
    expect(screen.getByText(/An index is not traded/)).toBeVisible()
  })

  it('links to the full terminal for the same instrument', async () => {
    await opened()
    expect(screen.getByRole('link', { name: 'Open in full terminal' })).toHaveAttribute(
      'href',
      '/trading?symbol=SBIN&exchange=NSE'
    )
  })

  it('offers to keep the screen on only where the phone can', async () => {
    await opened()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('offers to keep the screen on where it can', async () => {
    Object.defineProperty(navigator, 'wakeLock', {
      configurable: true,
      value: { request: vi.fn() },
    })
    await opened()
    expect(screen.getByRole('switch', { name: 'Keep screen on' })).toBeVisible()
  })

  describe('without prices', () => {
    beforeEach(() => {
      // A re-check that cannot reach the server leaves the stores as they are.
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => Promise.reject(new Error('offline')))
      )
    })
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('still checking: a quiet line, never the API key message', async () => {
      useAuthStore.setState({ apiKey: null, sessionChecked: false })
      renderAt('/chart/NSE/SBIN')
      expect(await screen.findByText('Checking your login')).toBeVisible()
      expect(screen.queryByText(/API key/)).toBeNull()
    })

    it('Upstox not logged in today: says so with the two links', async () => {
      useAuthStore.setState({ apiKey: null, sessionChecked: true, brokerSessionExpired: true })
      renderAt('/chart/NSE/SBIN')
      expect(await screen.findByText(/Upstox is not logged in today/)).toBeVisible()
      expect(screen.getByRole('link', { name: 'Auto login' })).toBeVisible()
      expect(screen.queryByText(/API key/)).toBeNull()
    })

    it('no API key: says what to do', async () => {
      useAuthStore.setState({ apiKey: null, sessionChecked: true, brokerSessionExpired: false })
      renderAt('/chart/NSE/SBIN')
      expect(await screen.findByText('Prices and search need an API key.')).toBeVisible()
      expect(screen.getByRole('link', { name: 'Create one under API Key' })).toHaveAttribute(
        'href',
        '/apikey'
      )
    })
  })

  it('ready but the stream settings did not come back: Try again, not an API key message', async () => {
    fakes.config = null
    renderAt('/chart/NSE/SBIN')
    expect(await screen.findByText('The chart could not start.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
    expect(screen.queryByText(/API key/)).toBeNull()
  })

  it('shows a failed load in words, with Try again and Dismiss', async () => {
    await opened()
    const failed: ChartStateView = {
      kind: 'error',
      symbol: 'SBIN',
      interval: '5m',
      message: 'The broker did not return bars',
    }
    emit((cb) => cb.onLoad(failed))
    const card = await screen.findByRole('alert')
    expect(card).toHaveTextContent('Could not load SBIN on 5m')
    expect(card).toHaveTextContent('The broker did not return bars. Try again in a moment.')
    chart().load.mockClear()
    await userEvent.click(within(card).getByRole('button', { name: 'Try again' }))
    expect(chart().load).toHaveBeenCalledWith('SBIN', 'NSE', '5m')
    emit((cb) => cb.onLoad(failed))
    await userEvent.click(
      within(await screen.findByRole('alert')).getByRole('button', { name: 'Dismiss' })
    )
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('switches instrument from search', async () => {
    await opened()
    await userEvent.click(screen.getByRole('button', { name: 'Search instruments' }))
    await userEvent.click(screen.getByRole('button', { name: 'RELIANCE' }))
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
      '/chart/NSE/RELIANCE'
    )
  })

  it('reads a candle under a held finger: when, O H L C and V', async () => {
    await opened()
    const surface = screen.getByTestId('chart-surface')
    const down = new MouseEvent('pointerdown', { bubbles: true, clientX: 120, clientY: 200 })
    Object.assign(down, { pointerId: 1, pointerType: 'touch' })
    fireEvent(surface, down)
    const readout = await screen.findByTestId('chart-readout', {}, { timeout: 2000 })
    expect(readout).toHaveTextContent('20 Oct, 10:15')
    for (const text of ['O812.50', 'H815.25', 'L811.00', 'C814.10', 'V12.35L']) {
      expect(readout.textContent?.replace(/\s/g, '')).toContain(text)
    }
    await userEvent.click(screen.getByRole('button', { name: 'Close the candle reading' }))
    expect(screen.queryByTestId('chart-readout')).toBeNull()
  })

  it('buys at a held price: the ticket opens with it as a limit, on the same product (C10)', async () => {
    rememberProduct('NSE', 'MIS')
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toBeEnabled())
    await hold()
    expect(chart().priceAt).toHaveBeenLastCalledWith(200)
    expect(screen.getByTestId('chart-held-price')).toHaveTextContent('814.35')
    const buy = screen.getByRole('button', { name: 'Buy at 814.35' })
    // The finger's touch on the reading's own button leaves the reading up, so the tap lands.
    touch(buy)
    await userEvent.click(buy)
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE MIS 1 chart-trading LIMIT 814.35'
    )
    expect(screen.queryByTestId('chart-readout')).toBeNull()
    // Back closes the ticket and stays on the chart; Buy from the footer is at the market again.
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'BUY' }))
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE MIS 1 chart-trading MARKET no price'
    )
  })

  it('takes no tap on the reading in its first half second: the lift of the finger that held is not a press', async () => {
    OPEN_GUARD.ms = 500
    await opened()
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toBeEnabled())
    await hold()
    fireEvent.click(screen.getByRole('button', { name: 'Buy at 814.35' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await act(() => new Promise((resolve) => setTimeout(resolve, 520)))
    fireEvent.click(screen.getByRole('button', { name: 'Buy at 814.35' }))
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent('LIMIT 814.35')
  })

  it("sells at a held price written in the instrument's decimals", async () => {
    fakes.heldPrice = 1234.5
    await opened()
    await waitFor(() => expect(screen.getByRole('button', { name: 'SELL' })).toBeEnabled())
    await hold()
    await userEvent.click(screen.getByRole('button', { name: 'Sell at 1,234.50' }))
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'SELL SBIN NSE CNC 1 chart-trading LIMIT 1234.5'
    )
  })

  it('offers no order at a held price over the volume or a study, where no price of it is drawn', async () => {
    fakes.heldPrice = null
    await opened()
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toBeEnabled())
    await hold()
    expect(screen.queryByRole('button', { name: /^(Buy|Sell) at/ })).toBeNull()
  })

  it('offers no order at a held price on an index, which is not traded', async () => {
    fakes.heldPrice = 25_912.35
    await opened('/chart/NSE_INDEX/NIFTY')
    await hold()
    expect(screen.queryByRole('button', { name: /^(Buy|Sell) at/ })).toBeNull()
  })

  it('moves the price on a tick alone, at the next frame', async () => {
    await opened()
    emit((cb) =>
      cb.onTick({ ltp: 812.35, time: null, tradeTime: null, at: Date.now(), polled: false })
    )
    await waitFor(() => expect(screen.getByTestId('chart-ltp')).toHaveTextContent('812.35'))
  })

  it('says when the session closes in place of a countdown that would run past it', async () => {
    await opened()
    emit((cb) => cb.onStatus(status({ kind: 'live' })))
    emit((cb) => cb.onSessionClose?.(Date.UTC(2026, 9, 20, 10, 0) / 1000))
    expect(screen.getByTestId('chart-closes')).toHaveTextContent('· Closes 15:30')
    emit((cb) => cb.onSessionClose?.(null))
    expect(screen.queryByTestId('chart-closes')).toBeNull()
  })

  it("closes the order ticket and the search with the phone's Back before leaving the chart", async () => {
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    const buy = await screen.findByRole('button', { name: 'BUY' })
    await waitFor(() => expect(buy).toBeEnabled())
    await userEvent.click(buy)
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')
    await userEvent.click(screen.getByRole('button', { name: 'Search instruments' }))
    expect(screen.getByRole('dialog', { name: 'Symbol Search' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/dashboard')
  })

  it('closes the ticket on an order placed, and stays on the chart', async () => {
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    const sell = await screen.findByRole('button', { name: 'SELL' })
    await waitFor(() => expect(sell).toBeEnabled())
    await userEvent.click(sell)
    await userEvent.click(screen.getByRole('button', { name: 'Place order' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/dashboard')
  })

  it("puts a new instrument in the chart's own place, so Back leaves the chart", async () => {
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    await userEvent.click(screen.getByRole('button', { name: 'Search instruments' }))
    await userEvent.click(screen.getByRole('button', { name: 'RELIANCE' }))
    await waitFor(() =>
      expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
        '/chart/NSE/RELIANCE'
      )
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/dashboard')
  })

  it('says why when the phone will not keep the screen on, and the switch goes off (C12)', async () => {
    let allow = false
    const request = vi.fn(async () => {
      if (!allow) throw new Error('battery saver is on')
      return { release: vi.fn(async () => {}), addEventListener() {} }
    })
    Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request } })
    await opened()
    const toggle = screen.getByRole('switch', { name: 'Keep screen on' })
    await userEvent.click(toggle)
    await waitFor(() => expect(toggle).not.toBeChecked())
    const words =
      'The phone would not keep the screen on. If battery saver is on, turn it off, then switch this on again.'
    expect(screen.getByText(words)).toBeVisible()
    expect(toggle).toHaveAccessibleDescription(words)
    // Switched on again once the phone allows it: the lock is held and the sentence goes.
    allow = true
    await userEvent.click(toggle)
    await waitFor(() => expect(toggle).toBeChecked())
    expect(screen.queryByText(/would not keep the screen on/)).toBeNull()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it('keeps the screen on across a switch of instrument', async () => {
    const release = vi.fn(async () => {})
    const request = vi.fn(async () => ({ release, addEventListener() {} }))
    Object.defineProperty(navigator, 'wakeLock', { configurable: true, value: { request } })
    await opened()
    await userEvent.click(screen.getByRole('switch', { name: 'Keep screen on' }))
    await waitFor(() => expect(request).toHaveBeenCalledOnce())
    await userEvent.click(screen.getByRole('button', { name: 'Search instruments' }))
    await userEvent.click(screen.getByRole('button', { name: 'RELIANCE' }))
    await waitFor(() =>
      expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
        '/chart/NSE/RELIANCE'
      )
    )
    expect(screen.getByRole('switch', { name: 'Keep screen on' })).toBeChecked()
    expect(release).not.toHaveBeenCalled()
  })

  it('gives every control a target a thumb can hit: 48 px at least', async () => {
    await opened()
    const buttons = screen.getAllByRole('button')
    expect(buttons.length).toBeGreaterThan(8)
    for (const button of buttons) {
      expect(button.className, button.textContent ?? '').toMatch(/\b(min-)?h-12\b|\bsize-12\b/)
    }
  })

  it('opens the last instrument for a bare /chart, or NIFTY on a new phone', async () => {
    renderAt('/chart')
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
      '/chart/NSE_INDEX/NIFTY'
    )
  })

  it('opens the instrument last opened on this phone for a bare /chart', async () => {
    localStorage.setItem('sudarshan-chart-last', '{"symbol":"BANKNIFTY","exchange":"NSE_INDEX"}')
    renderAt('/chart')
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
      '/chart/NSE_INDEX/BANKNIFTY'
    )
  })

  it("draws the screen again for what it shows, not for the app's market data clock (C14)", async () => {
    // The app's provider makes a new value every second around the same pages,
    // which only its readers hear; the screen, sheets and all, must not follow it.
    const value = () => ({ manager: MarketDataManager.getInstance() }) as never
    let tickClock: () => void = () => {}
    const pages = (
      <MemoryRouter initialEntries={['/chart/NSE/SBIN']}>
        <Routes>
          <Route path="/chart/:exchange/:symbol" element={<ChartPage />} />
        </Routes>
      </MemoryRouter>
    )
    function Provider() {
      const [context, setContext] = useState(value)
      tickClock = () => setContext(value())
      return <MarketDataContext.Provider value={context}>{pages}</MarketDataContext.Provider>
    }
    render(
      <QueryClientProvider client={createQueryClient()}>
        <Provider />
      </QueryClientProvider>
    )
    await waitFor(() => expect(chart().load).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toBeEnabled())
    // Each drawing of the screen reads the indicator rows once.
    const drawn = chart().legendRows.mock.calls.length
    for (let second = 0; second < 3; second++) act(() => tickClock())
    expect(chart().legendRows.mock.calls.length).toBe(drawn)
    // What it shows still draws it: a sheet opening.
    await userEvent.click(screen.getByRole('button', { name: 'Chart style: Candles' }))
    expect(chart().legendRows.mock.calls.length).toBeGreaterThan(drawn)
  })

  it('lets the chart go when the page closes', async () => {
    const { unmount } = await opened()
    unmount()
    expect(chart().destroy).toHaveBeenCalled()
  })
})

describe('a market alert from the phone chart (M10.2)', () => {
  const tick = (ltp: number) =>
    emit((cb) => cb.onTick({ ltp, time: null, tradeTime: null, at: Date.now(), polled: false }))

  it('opens the alert form from the bell, at the last price, and Back closes it first', async () => {
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    tick(812.4)

    await userEvent.click(screen.getByRole('button', { name: 'Set alert' }))
    const form = screen.getByRole('dialog', { name: 'New alert' })
    expect(within(form).getByLabelText('Price level')).toHaveValue('812.40')

    // The open sheet hides the page around it, so the phone's Back is pressed as the phone would.
    fireEvent.click(screen.getByRole('button', { name: 'Phone back', hidden: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')
    await userEvent.click(screen.getByRole('button', { name: 'Phone back' }))
    expect(screen.getByText('Elsewhere')).toBeInTheDocument()
  })

  it('says the alert is set, with View to it, and closes the form', async () => {
    alerting.create.mockReset().mockResolvedValue({
      id: 77,
      symbol: 'SBIN',
      exchange: 'NSE',
      words: { condition: 'Crosses above ₹812.40', meta: 'Once', checks_at: null },
    })
    alerting.toasts.success.mockReset()
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    tick(812.4)
    await userEvent.click(screen.getByRole('button', { name: 'Set alert' }))
    await userEvent.click(
      within(screen.getByRole('dialog', { name: 'New alert' })).getByRole('button', {
        name: 'Save',
      })
    )

    await waitFor(() => expect(alerting.toasts.success).toHaveBeenCalledTimes(1))
    expect(alerting.create.mock.calls[0][0]).toMatchObject({
      symbol: 'SBIN',
      exchange: 'NSE',
      params: { op: 'crosses_above', level: '812.40', source: 'ltp' },
      price: 812.4,
    })
    expect(alerting.toasts.success.mock.calls[0][0]).toBe('Alert set: SBIN')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')

    act(() => alerting.toasts.success.mock.calls[0][1].action.onClick())
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent(
      '/market-alerts?alert=77'
    )
  })

  it('closes the form’s instrument search first on Back, and a pick goes back once', async () => {
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    tick(812.4)
    await userEvent.click(screen.getByRole('button', { name: 'Set alert' }))
    const form = screen.getByRole('dialog', { name: 'New alert' })

    fireEvent.click(within(form).getByRole('button', { name: /Change/ }))
    expect(screen.getByRole('dialog', { name: 'Symbol Search' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Phone back', hidden: true }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Symbol Search' })).toBeNull())
    expect(screen.getByRole('dialog', { name: 'New alert' })).toBeInTheDocument()

    fireEvent.click(within(form).getByRole('button', { name: /Change/ }))
    fireEvent.click(screen.getByRole('button', { name: 'RELIANCE' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Symbol Search' })).toBeNull())
    expect(within(form).getByRole('button', { name: /RELIANCE/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Phone back', hidden: true }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New alert' })).toBeNull())
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')
  })

  it('does not reopen an alert form with the page: it opens only from a tap', async () => {
    await opened({ pathname: '/chart/NSE/SBIN', state: { sheet: 'alert' } }, ['/chart/NSE/SBIN'])

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'New alert' })).toBeNull())
    expect(screen.getByRole('status', { name: 'Location' })).toHaveTextContent('/chart/NSE/SBIN')
  })

  it('offers no bell where market alerts do not reach', async () => {
    await opened('/chart/MCX/GOLDM')
    expect(screen.queryByRole('button', { name: 'Set alert' })).toBeNull()
  })
})

describe('the phone on its side (C10)', () => {
  let held: ReturnType<typeof phoneHeld>
  afterEach(() => held.spy.mockRestore())

  it('folds the chrome into one strip: back, the price and its state, timeframe, tools, Buy and Sell', async () => {
    held = phoneHeld('sideways')
    await opened()
    const strip = screen.getByTestId('chart-strip')
    emit((cb) => {
      cb.onTick({ ltp: 1234.5, time: null, tradeTime: null, at: Date.now(), polled: false })
      cb.onPrevClose?.(1200)
      cb.onStatus(status({ kind: 'live' }))
    })
    expect(within(strip).getByTestId('chart-ltp')).toHaveTextContent('1,234.50')
    const change = within(strip).getByTestId('chart-change')
    expect(change).toHaveTextContent('+₹34.50 (+2.88%)')
    expect(change.querySelector('[data-direction="up"]')).not.toBeNull()
    expect(within(strip).getByText('Real-time')).toBeVisible()
    expect(within(strip).getByRole('heading', { name: 'SBIN' })).toBeVisible()
    await waitFor(() => expect(within(strip).getByRole('button', { name: 'BUY' })).toBeEnabled())
    expect(within(strip).getByRole('button', { name: 'SELL' })).toBeEnabled()
    // The upright layout's rows are folded away; the chart takes the rest.
    for (const gone of ['Search instruments', 'More timeframes', 'Set alert'])
      expect(screen.queryByRole('button', { name: gone })).toBeNull()
    expect(screen.queryByTestId('depth-strip')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Open in full terminal' })).toBeNull()
    await userEvent.click(within(strip).getByRole('button', { name: 'Timeframe 5m, change' }))
    expect(screen.getByRole('dialog', { name: 'Timeframe' })).toBeVisible()
    fireEvent.click(screen.getByText('Phone back'))
    await userEvent.click(within(strip).getByRole('button', { name: 'Chart style: Candles' }))
    expect(screen.getByRole('dialog', { name: 'Chart style' })).toBeVisible()
    fireEvent.click(screen.getByText('Phone back'))
    await userEvent.click(within(strip).getByRole('button', { name: 'SELL' }))
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'SELL SBIN NSE CNC 1 chart-trading MARKET no price'
    )
  })

  it('keeps the chart it has when the phone is turned, either way', async () => {
    held = phoneHeld('upright')
    await opened()
    const surface = screen.getByTestId('chart-surface')
    expect(screen.queryByTestId('chart-strip')).toBeNull()
    held.turn('sideways')
    expect(screen.getByTestId('chart-strip')).toBeVisible()
    expect(screen.getByTestId('chart-surface')).toBe(surface)
    held.turn('upright')
    expect(screen.queryByTestId('chart-strip')).toBeNull()
    expect(screen.getByRole('button', { name: 'Search instruments' })).toBeVisible()
    expect(screen.getByTestId('chart-surface')).toBe(surface)
    expect(fakes.charts).toHaveLength(1)
    expect(chart().destroy).not.toHaveBeenCalled()
    expect(chart().load).toHaveBeenCalledOnce()
  })

  it('reads a candle and buys at a held price on its side too', async () => {
    held = phoneHeld('sideways')
    await opened()
    await waitFor(() => expect(screen.getByRole('button', { name: 'BUY' })).toBeEnabled())
    await hold()
    await userEvent.click(screen.getByRole('button', { name: 'Buy at 814.35' }))
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE CNC 1 chart-trading LIMIT 814.35'
    )
  })

  it('has no Buy or Sell for an index on its side', async () => {
    held = phoneHeld('sideways')
    await opened('/chart/NSE_INDEX/NIFTY')
    const strip = screen.getByTestId('chart-strip')
    expect(within(strip).queryByRole('button', { name: 'BUY' })).toBeNull()
    expect(within(strip).queryByRole('button', { name: 'SELL' })).toBeNull()
  })

  it('gives every control on the strip a target a thumb can hit: 48 px at least', async () => {
    held = phoneHeld('sideways')
    await opened()
    const buttons = within(screen.getByTestId('chart-strip')).getAllByRole('button')
    expect(buttons).toHaveLength(6)
    for (const button of buttons)
      expect(button.className, button.textContent ?? '').toMatch(/\b(min-)?h-12\b|\bsize-12\b/)
  })
})

describe('the lines of the charted instrument (C5)', () => {
  const HELD = {
    symbol: 'SBIN',
    exchange: 'NSE',
    product: 'MIS',
    quantity: 10,
    average_price: 810,
    ltp: 812,
    pnl: 20,
    pnlpercent: 0.25,
  }
  const STOP = {
    symbol: 'SBIN',
    exchange: 'NSE',
    action: 'SELL',
    quantity: 10,
    price: 0,
    trigger_price: 800,
    pricetype: 'SL-M',
    product: 'MIS',
    orderid: 'A1',
    order_status: 'trigger pending',
    timestamp: '2026-11-16 10:41:51',
  }

  it('lays the position, its working order and its price alert over the chart, each with its words', async () => {
    fakes.positions = [HELD, { ...HELD, symbol: 'INFY' }]
    fakes.orders = [STOP, { ...STOP, orderid: 'A2', order_status: 'complete' }]
    fakes.alerts = [
      {
        id: 7,
        status: 'active',
        kind: 'price',
        symbol: 'SBIN',
        exchange: 'NSE',
        params: { op: 'at_or_above', level: '830' },
      },
    ]
    await opened()
    await waitFor(() =>
      expect(
        (chart().setOverlays.mock.lastCall?.[0] as { lines: Array<{ label: string }> }).lines.map(
          (line) => line.label
        )
      ).toEqual(['Buy 10 @ 810.00', 'SELL 10 SL-M 800.00', 'Alert ≥ 830.00'])
    )
    act(() =>
      chart().moveLines([
        { id: 'position:MIS', y: 200 },
        { id: 'order:A1:trigger', y: 300 },
        { id: 'alert:7', y: 80 },
      ])
    )
    const lines = within(screen.getByTestId('chart-lines'))
    expect(lines.getByText('Buy 10 @ 810.00')).toBeVisible()
    expect(lines.getByText('SELL 10 SL-M 800.00')).toBeVisible()
    expect(lines.getByText('Alert ≥ 830.00')).toBeVisible()
    // The position's P&L is the book's until a tick moves it, then moves with the tick.
    const label = lines.getByText('Buy 10 @ 810.00').closest('[data-line]') as HTMLElement
    expect(label).toHaveTextContent('+₹20.00')
    act(() =>
      chart().opts.callbacks.onTick({
        ltp: 815,
        time: null,
        tradeTime: null,
        at: Date.now() + 1000,
        polled: false,
      })
    )
    await waitFor(() => expect(label).toHaveTextContent('+₹50.00'))
  })

  async function withLines() {
    fakes.positions = [HELD]
    fakes.orders = [STOP]
    fakes.alerts = [
      {
        id: 7,
        version: 2,
        status: 'active',
        kind: 'price',
        symbol: 'SBIN',
        exchange: 'NSE',
        tick_size: '0.05',
        params: { op: 'at_or_above', level: '830' },
        frequency: 'once',
        cooldown_s: 0,
        ends_at: null,
        candle_min: null,
        note: '',
        channels: null,
        words: { condition: 'SBIN is at or above 830', meta: '', checks_at: null },
      },
    ]
    await opened('/chart/NSE/SBIN', ['/dashboard'])
    await waitFor(() => expect(chart().setOverlays.mock.lastCall?.[0].lines).toHaveLength(3))
    act(() =>
      chart().moveLines([
        { id: 'position:MIS', y: 200 },
        { id: 'order:A1:trigger', y: 300 },
        { id: 'alert:7', y: 80 },
      ])
    )
  }

  it("opens a line's sheet from its label, as an entry the phone's Back closes first (C6)", async () => {
    await withLines()
    await userEvent.click(
      screen.getByRole('button', { name: /Buy 10 @ 810\.00.*Open its actions/ })
    )
    const sheet = screen.getByRole('dialog', { name: 'SBIN' })
    expect(within(sheet).getByRole('button', { name: /^Exit/ })).toHaveTextContent(
      'Sell 10 at market'
    )
    fireEvent.click(screen.getByText('Phone back'))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull())
    expect(screen.getByLabelText('Location')).toHaveTextContent('/chart/NSE/SBIN')
  })

  it("opens the app's order ticket from Add, on the position's side and in its product", async () => {
    await withLines()
    await userEvent.click(
      screen.getByRole('button', { name: /Buy 10 @ 810\.00.*Open its actions/ })
    )
    await userEvent.click(
      within(screen.getByRole('dialog', { name: 'SBIN' })).getByRole('button', { name: /^Add/ })
    )
    expect(screen.getByRole('dialog', { name: 'Order ticket' })).toHaveTextContent(
      'BUY SBIN NSE MIS 1 chart-trading MARKET no price'
    )
    expect(screen.queryByRole('dialog', { name: 'SBIN' })).toBeNull()
    // The ticket took the sheet's entry: one Back is the chart again.
    fireEvent.click(screen.getByText('Phone back'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByLabelText('Location')).toHaveTextContent('/chart/NSE/SBIN')
  })

  it("offers Modify and Cancel on an order's line, and the alert's own form on an alert's", async () => {
    await withLines()
    await userEvent.click(
      screen.getByRole('button', { name: /SELL 10 SL-M 800\.00.*Open its actions/ })
    )
    const order = within(screen.getByRole('dialog', { name: 'SBIN' }))
    expect(order.getByRole('button', { name: 'Modify' })).toBeEnabled()
    expect(order.getByRole('button', { name: 'Cancel order' })).toBeEnabled()
    fireEvent.click(screen.getByText('Phone back'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await userEvent.click(screen.getByRole('button', { name: /Alert ≥ 830\.00.*Open its actions/ }))
    await userEvent.click(
      within(screen.getByRole('dialog', { name: 'SBIN' })).getByRole('button', { name: 'Edit' })
    )
    const form = await screen.findByRole('dialog', { name: /alert/i })
    expect(within(form).getByLabelText('Price level')).toHaveValue('830')
  })

  it('reads no book for an index, and draws no line on it', async () => {
    const { tradingApi } = await import('@/api/trading')
    vi.mocked(tradingApi.getPositions).mockClear()
    fakes.positions = [{ ...HELD, symbol: 'NIFTY', exchange: 'NSE_INDEX' }]
    await opened('/chart/NSE_INDEX/NIFTY')
    expect(tradingApi.getPositions).not.toHaveBeenCalled()
    expect(screen.queryByTestId('chart-lines')).toBeNull()
  })
})
