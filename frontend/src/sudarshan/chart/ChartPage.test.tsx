import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartStateView } from '@/lib/trading/chartState'
import type { LiveStatus } from '@/lib/trading/liveStatus'
import type { FeedLine } from '@/sudarshan/dashboard/live'
import { rememberProduct } from '@/sudarshan/ticket/productPref'
import type { PhoneChartCallbacks, PhoneTick } from './phoneChart'

const fakes = vi.hoisted(() => ({
  charts: [] as Array<{
    opts: { callbacks: PhoneChartCallbacks; appMode: string }
    load: ReturnType<typeof vi.fn>
    setAppMode: ReturnType<typeof vi.fn>
    destroy: ReturnType<typeof vi.fn>
    barAt: ReturnType<typeof vi.fn>
    barX: ReturnType<typeof vi.fn>
  }>,
  config: { apiKey: 'test-key', wsUrl: 'ws://test.invalid' } as unknown,
  instrument: { name: 'STATE BANK OF INDIA', lotSize: 1, tickSize: 0.05 } as unknown,
}))

vi.mock('./phoneChart', () => ({
  isQuoteOnly: (exchange: string) => exchange.endsWith('_INDEX'),
  PhoneChart: class {
    opts: unknown
    load = vi.fn(async () => {})
    setAppMode = vi.fn()
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
    constructor(opts: unknown) {
      this.opts = opts
      fakes.charts.push(this as never)
    }
  },
}))
vi.mock('./api', () => ({
  loadFeedConfig: async () => fakes.config,
  loadInstrument: async () => fakes.instrument,
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
  }) =>
    props.open ? (
      <div role="dialog" aria-label="Order ticket">
        {[props.action, props.symbol, props.exchange, props.product, props.lotSize, props.strategy]
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
  toasts: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('@/api/marketAlerts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketAlerts')>()),
  marketAlertsApi: { create: alerting.create },
}))
vi.mock('@/utils/toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/toast')>()),
  toast: alerting.toasts,
}))

import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/providers'
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

function renderAt(path: string, before: string[] = []) {
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

async function opened(path = '/chart/NSE/SBIN', before: string[] = []) {
  const view = renderAt(path, before)
  await waitFor(() => expect(fakes.charts.length).toBeGreaterThan(0))
  await waitFor(() => expect(chart().load).toHaveBeenCalled())
  return view
}

beforeEach(() => {
  fakes.charts.length = 0
  fakes.config = { apiKey: 'test-key', wsUrl: 'ws://test.invalid' }
  fakes.instrument = { name: 'STATE BANK OF INDIA', lotSize: 1, tickSize: 0.05 }
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

  it('says what to do when there is no API key', async () => {
    fakes.config = null
    renderAt('/chart/NSE/SBIN')
    expect(await screen.findByText('The chart needs your API key.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open the API key page' })).toHaveAttribute(
      'href',
      '/apikey'
    )
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

  it('offers no bell where market alerts do not reach', async () => {
    await opened('/chart/MCX/GOLDM')
    expect(screen.queryByRole('button', { name: 'Set alert' })).toBeNull()
  })
})
