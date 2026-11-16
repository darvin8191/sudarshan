/**
 * Trade markers on the phone chart (M14.8) and the order ticket's charges line (M5.10), at
 * 360 x 800 in a real browser with the real chart engine. Nothing reaches a server: every REST
 * answer is a fixture here, the journal's included, and every address that places, changes or
 * cancels an order is answered with a refusal and counted, so a test that sent one fails. The
 * page's clock is Tuesday 20 October 2026 in India, 13:00. Every value is synthetic.
 *
 * Chromium only, as the chart's own spec:
 *   npx playwright test e2e/sd-trip-charges.spec.ts --project=chromium
 * A screenshot of each state is kept for the owner's eye.
 */
import { expect, type Page, type Route, test } from '@playwright/test'

test.skip(({ browserName }) => browserName !== 'chromium', 'the chart spec runs on Chromium only')
test.use({ viewport: { width: 360, height: 800 }, hasTouch: true, isMobile: true })

/** Epoch milliseconds for a time of day in India in October 2026 (the 20th unless said). */
const ist = (h: number, m: number, s = 0, day = 20) =>
  Date.UTC(2026, 9, day, h, m, 0) + Math.round(s * 1000) - 19_800_000
const NOW = ist(13, 0)
const TRADING_DAYS = [13, 14, 15, 16, 19, 20]

/** A seeded walk, so every run draws the same candles. */
function walk(seed: number) {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296
  }
}

/** History up to the page's time: minutes over five sessions and today, days over October. */
function history(interval: string) {
  const step = { '1m': 60, '3m': 180, '5m': 300, '15m': 900, '1h': 3600 }[interval]
  const random = walk(812)
  let price = 800.4
  const rows: Array<Record<string, number>> = []
  const push = (time: number) => {
    const open = price
    const close = Math.round((open + (random() - 0.5) * 2.4) * 20) / 20
    const high = Math.max(open, close) + Math.round(random() * 0.8 * 20) / 20
    const low = Math.min(open, close) - Math.round(random() * 0.8 * 20) / 20
    price = close
    rows.push({ timestamp: time, open, high, low, close, volume: 20_000 + Math.round(random() * 60_000) })
  }
  if (!step) {
    for (let d = 1; d <= 20; d++) {
      const weekday = new Date(Date.UTC(2026, 9, d)).getUTCDay()
      if (weekday !== 0 && weekday !== 6) push(Date.UTC(2026, 9, d) / 1000 - 19_800)
    }
    return rows
  }
  for (const day of TRADING_DAYS)
    for (let t = ist(9, 15, 0, day) / 1000; t < ist(15, 30, 0, day) / 1000; t += step) {
      if (t * 1000 > NOW) return rows
      push(t)
    }
  return rows
}

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

/** Every address that places, changes or cancels an order: answered with a refusal, counted. */
const ORDER_ADDRESSES = [
  'placeorder',
  'placesmartorder',
  'basketorder',
  'splitorder',
  'modifyorder',
  'cancelorder',
  'cancelallorder',
  'closeposition',
  'optionsorder',
  'optionsmultiorder',
  'placegttorder',
  'modifygttorder',
  'cancelgttorder',
  'close_all_positions',
  'cancel_all_orders',
  'close_position',
  'cancel_order',
  'modify_order',
]

/** A trade of SBIN as the journal's trip_get answers it. */
function trade(id: number, entryMs: number, exitMs: number, day: number) {
  const fill = (n: number, action: string, at: number, price: number) => ({
    trade_id: `TEST-${id}-${n}`,
    order_id: `ORD-${id}-${n}`,
    mode: 'live',
    day,
    exchange: 'NSE',
    symbol: 'SBIN',
    instrument_key: 'NSE_EQ|TEST0000001',
    product: 'MIS',
    action,
    qty: 10,
    price_u: Math.round(price * 10_000),
    exch_ts_ms: at,
    order_ts_ms: null,
    source: 'stream_read',
    surface: 'app',
    trip_qty: 10,
  })
  const leg = (n: number, role: string, action: string, at: number, price: number) => ({
    trade_id: `TEST-${id}-${n}`,
    order_id: `ORD-${id}-${n}`,
    role,
    action,
    qty: 10,
    price_u: Math.round(price * 10_000),
    exch_ts_ms: at,
    day,
    source: 'stream_read',
  })
  return {
    id,
    mode: 'live',
    exchange: 'NSE',
    symbol: 'SBIN',
    instrument_key: 'NSE_EQ|TEST0000001',
    product: 'MIS',
    product_class: 'intraday',
    side: 'long',
    qty_max: 10,
    entry_qty: 10,
    exit_qty: 10,
    entry_avg_u: 8_030_500,
    exit_avg_u: 8_094_000,
    opened_ms: entryMs,
    closed_ms: exitMs,
    open_day: day,
    close_day: day,
    status: 'closed',
    gross_paise: 6350,
    charges_est_paise: 4082,
    charges_actual_paise: null,
    net_paise: 2268,
    hold_s: Math.round((exitMs - entryMs) / 1000),
    strategy: 'ORB',
    strategy_override: null,
    setup_id: null,
    own_expiry_day: false,
    index_expiry_day: false,
    risk_paise: null,
    mae_paise: null,
    mfe_paise: null,
    mae_ms: null,
    mfe_ms: null,
    excursion_source: 'none',
    excursion_note: null,
    version: 1,
    charges_modelled: true,
    charges_actual_basis: null,
    legs: [leg(1, 'entry', 'BUY', entryMs, 803.05), leg(2, 'exit', 'SELL', exitMs, 809.4)],
    fills: [fill(1, 'BUY', entryMs, 803.05), fill(2, 'SELL', exitMs, 809.4)],
    note: null,
    excursion: null,
    charges: null,
    charges_actual: null,
    latency_ms: null,
    orders: [],
  }
}

const TRADES: Record<number, unknown> = {
  7: trade(7, ist(10, 2, 5), ist(10, 41, 50), 20261020),
  8: trade(8, ist(10, 0, 0, 1), ist(10, 30, 0, 1), 20261001),
}

/** Trade 9 while it is still open (its entry only), and once its exit is in the journal. */
const EXITED_9 = trade(9, ist(12, 48, 5), ist(12, 57, 30), 20261020)
const OPEN_9 = {
  ...EXITED_9,
  status: 'open',
  closed_ms: null,
  exit_qty: 0,
  legs: EXITED_9.legs.slice(0, 1),
  fills: EXITED_9.fills.slice(0, 1),
}

const ESTIMATE = {
  segment: 'equity_delivery',
  modelled: true,
  plan: 'plus',
  parts: {
    brokerage_paise: 3000,
    stt_paise: 803,
    exchange_paise: 25,
    sebi_paise: 1,
    stamp_paise: 120,
    gst_paise: 545,
    total_paise: 4494,
  },
  turnover_paise: 803050,
  day: 20261020,
  rate_card: { version: '2026-11-14.2', from_day: 20260401 },
  left_out: [],
}

interface Fixture {
  /** The journal is switched off on the server. */
  journalOff?: boolean
}

/** What the server pushes over the app's connection, when a test says so. */
let push: (event: string, frame: unknown) => Promise<void> = async () => {}
/** Trade 9's exit has reached the journal. */
let exited9 = false

/** What the test in hand caught: order calls, requests to other hosts, and the journal's ops. */
let caught = {
  orders: [] as string[],
  outside: [] as string[],
  journal: [] as string[],
  history: [] as string[],
  /** Requests to the app's server that no fixture below answers: refused, and listed. */
  unrouted: [] as string[],
}
test.beforeEach(() => {
  caught = { orders: [], outside: [], journal: [], history: [], unrouted: [] }
  exited9 = false
})
test.afterEach(() => {
  expect(caught.outside, 'a request to another host').toEqual([])
  expect(caught.orders, 'an order call').toEqual([])
  expect(caught.unrouted, 'a request no fixture answers').toEqual([])
})

async function serve(page: Page, fixture: Fixture = {}) {
  await page.route(
    (url) => !['localhost', '127.0.0.1'].includes(url.hostname),
    (route) => {
      caught.outside.push(route.request().url())
      return route.abort()
    }
  )
  // Registered first, so every route below wins over it: nothing reaches the dev server's proxy.
  const appServer = /^\/(api|auth|sudarshan|socket\.io|custom-indicators)\//
  await page.route((url) => appServer.test(url.pathname), (route) => {
    caught.unrouted.push(new URL(route.request().url()).pathname)
    return route.fulfill(json({ status: 'error', message: 'Not answered in this test.' }, 404))
  })
  await page.clock.install({ time: NOW })
  let polls = 0
  /** The long poll the page is waiting on: a push answers it, and the page asks again. */
  let waiting: Route | null = null
  push = async (event, frame) => {
    await expect.poll(() => waiting !== null, 'the page is listening').toBe(true)
    const route = waiting as Route | null
    waiting = null
    await route?.fulfill({ body: `42${JSON.stringify([event, frame])}` })
  }
  await page.route('**/socket.io/**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'POST') return route.fulfill({ body: 'ok' })
    if (!url.searchParams.get('sid'))
      return route.fulfill({
        body: `0${JSON.stringify({ sid: 's1', upgrades: [], pingInterval: 600_000, pingTimeout: 600_000, maxPayload: 1_000_000 })}`,
      })
    if (polls++ === 0) return route.fulfill({ body: '40{"sid":"n1"}' })
    waiting = route
  })
  // Charts across devices (M4.18) ships switched off: the chart asks once and keeps to the phone.
  await page.route('**/sudarshan/charts/api/state', (route) =>
    route.fulfill(json({ status: 'ok', data: { enabled: false } }))
  )
  await page.route('**/auth/session-status*', (route) =>
    route.fulfill(
      json({
        status: 'success',
        authenticated: true,
        logged_in: true,
        user: 'owner',
        broker: 'upstox',
        api_key: 'test-key',
        broker_session_expired: false,
        active_sessions: 1,
      })
    )
  )
  await page.route('**/auth/csrf-token', (route) => route.fulfill(json({ csrf_token: 'token' })))
  await page.route('**/auth/app-info', (route) =>
    route.fulfill(json({ status: 'success', version: 'test' }))
  )
  // The kill switch not read: the ticket's note shows nothing for it, as on a failed read.
  await page.route('**/sudarshan/risk/state', (route) =>
    route.fulfill(json({ status: 'error', message: 'Not read in this test.' }, 503))
  )
  await page.route('**/auth/analyzer-mode', (route) =>
    route.fulfill(json({ status: 'success', data: { analyze_mode: true, mode: 'analyze' } }))
  )
  await page.route('**/api/broker/capabilities', (route) =>
    route.fulfill(json({ status: 'success', data: { broker_type: 'stock', broker_name: 'upstox' } }))
  )
  await page.route('**/api/websocket/apikey', (route) =>
    route.fulfill(json({ status: 'success', api_key: 'test-key' }))
  )
  await page.route('**/api/websocket/config', (route) =>
    route.fulfill(json({ status: 'success', websocket_url: 'ws://proxy.invalid/proxy' }))
  )
  await page.route('**/admin/api/timings', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: ['NSE', 'BSE', 'NFO', 'BFO'].map((exchange) => ({
          exchange,
          start_offset: 33_300_000,
          end_offset: 55_800_000,
        })),
      })
    )
  )
  await page.route('**/sudarshan/session/instrument*', (route) =>
    route.fulfill(json({ status: 'success', data: { day: '2026-10-20', stops_at: null, closing: null } }))
  )
  await page.route('**/sudarshan/alerts/api/state', (route) =>
    route.fulfill(json({ status: 'success', data: { enabled: true, alerts: [], groups: [] } }))
  )
  await page.route('**/sudarshan/journal/api/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/sudarshan/journal/api', '')
    caught.journal.push(`${path}${url.search}`)
    if (fixture.journalOff)
      return route.fulfill(
        json({ status: 'success', enabled: false, data: null, message: 'The journal is switched off on the server.' })
      )
    const id = /^\/trips\/(\d+)$/.exec(path)?.[1]
    if (id === '9')
      return route.fulfill(
        json({ status: 'success', enabled: true, data: exited9 ? EXITED_9 : OPEN_9 })
      )
    if (id && TRADES[Number(id)]) return route.fulfill(json({ status: 'success', enabled: true, data: TRADES[Number(id)] }))
    if (path === '/charges/estimate')
      return route.fulfill(json({ status: 'success', enabled: true, data: ESTIMATE }))
    if (path === '/state')
      return route.fulfill(
        json({
          status: 'success',
          enabled: true,
          data: { enabled: true, fills_today: 0, open_trips: 0, last_reconcile_ms: null, limits: {} },
        })
      )
    return route.fulfill(json({ status: 'success', enabled: true, data: [] }))
  })
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/', '')
    const body = (route.request().postDataJSON() ?? {}) as Record<string, string>
    if (ORDER_ADDRESSES.includes(path)) {
      caught.orders.push(path)
      return route.fulfill(json({ status: 'error', message: 'This is a test: no order was sent.' }, 400))
    }
    switch (path) {
      case 'positionbook':
        return route.fulfill(json({ status: 'success', data: [] }))
      case 'orderbook':
        return route.fulfill(json({ status: 'success', data: { orders: [], statistics: {} } }))
      case 'symbol':
        return route.fulfill(
          json({ status: 'success', data: { name: 'STATE BANK OF INDIA', lotsize: 1, tick_size: 0.05 } })
        )
      case 'intervals':
        return route.fulfill(
          json({
            status: 'success',
            data: {
              seconds: [],
              minutes: ['1m', '2m', '3m', '5m', '10m', '15m', '30m'],
              hours: ['1h', '2h', '3h', '4h'],
              days: ['D'],
              weeks: ['W'],
              months: ['M'],
            },
          })
        )
      case 'history':
        caught.history.push(body.interval)
        return route.fulfill(json({ status: 'success', data: history(body.interval) }))
      case 'quotes':
        return route.fulfill(
          json({ status: 'success', data: { ltp: Number(history('1m').at(-1)?.close), prev_close: 799.1, volume: 4_210_000 } })
        )
      case 'depth':
        return route.fulfill(
          json({
            status: 'success',
            data: { ltp: 803.05, prev_close: 799.1, bids: [{ price: 803, quantity: 1_240 }], asks: [{ price: 803.05, quantity: 860 }] },
          })
        )
      case 'margin':
        return route.fulfill(json({ status: 'success', data: { total_margin_required: 8030.5 } }))
      case 'funds':
        return route.fulfill(
          json({
            status: 'success',
            data: { availablecash: '250000.00', collateral: '0.00', m2mrealized: '0.00', m2munrealized: '0.00', utiliseddebits: '0.00' },
          })
        )
      default:
        return route.fulfill(json({ status: 'success', data: [] }))
    }
  })
  await page.routeWebSocket(/\/proxy$/, (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw)) as Record<string, unknown>
      if (message.action === 'authenticate') ws.send(JSON.stringify({ type: 'auth', status: 'success' }))
      else if (message.action === 'ping')
        ws.send(
          JSON.stringify({
            type: 'pong',
            status: 'success',
            server_timestamp: Number(message.timestamp),
            client_timestamp: message.timestamp,
            _pingId: message._pingId,
          })
        )
    })
  })
}

async function open(page: Page, path: string) {
  await page.goto(path)
  await expect(page.getByTestId('phone-chart')).toBeVisible()
  await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' })
}

async function noSidewaysScroll(page: Page) {
  const width = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(width.scroll, 'no sideways page scroll').toBeLessThanOrEqual(width.client)
}

/** The D tokens of a buy's and a sell's mark, as the canvas paints them. */
const BUY_INK = [0x1c, 0x65, 0xc8] as const
const SELL_INK = [0xd0, 0x2f, 0x14] as const

/** Where the chart's canvases are painted in a colour: the box of those pixels in CSS px, or null. */
async function tagBox(page: Page, ink: readonly [number, number, number]) {
  return page.evaluate(([r, g, b]) => {
    const surface = document.querySelector('[data-testid="chart-surface"]')
    if (!surface) return null
    let box: { x0: number; y0: number; x1: number; y1: number; count: number } | null = null
    for (const canvas of surface.querySelectorAll('canvas')) {
      const context = canvas.getContext('2d')
      if (!context || !canvas.width || !canvas.height) continue
      const rect = canvas.getBoundingClientRect()
      const scale = canvas.width / rect.width
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height)
      for (let y = 0; y < canvas.height; y++)
        for (let x = 0; x < canvas.width; x++) {
          const at = (y * canvas.width + x) * 4
          if (data[at + 3] < 250) continue
          if (Math.abs(data[at] - r) > 3 || Math.abs(data[at + 1] - g) > 3 || Math.abs(data[at + 2] - b) > 3)
            continue
          const px = rect.left + x / scale
          const py = rect.top + y / scale
          box = box
            ? { x0: Math.min(box.x0, px), y0: Math.min(box.y0, py), x1: Math.max(box.x1, px), y1: Math.max(box.y1, py), count: box.count + 1 }
            : { x0: px, y0: py, x1: px, y1: py, count: 1 }
        }
    }
    return box && { x: box.x0, y: box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0, count: box.count }
  }, ink)
}

/** How many pixels of the chart are painted in a colour. */
const inked = async (page: Page, ink: readonly [number, number, number]) =>
  (await tagBox(page, ink))?.count ?? 0

const shot = (page: Page, name: string) =>
  page.screenshot({ path: test.info().outputPath(`${name}.png`) })

test.describe('a trade on the phone chart (M14.8)', () => {
  test('the trade sheet opens the chart on minute candles with the trade marked', async ({ page }) => {
    await serve(page)
    await page.goto('/journal?trade=7')
    const link = page.getByRole('link', { name: 'See on chart' })
    await expect(link).toHaveAttribute('href', '/chart/NSE/SBIN?trip=7')
    // The sheet takes no tap in its first half second (M6.9): the tap comes after it.
    await page.clock.runFor(600)
    await expect(page.locator('[data-settling]')).toHaveCount(0)
    await link.click()
    await expect(page).toHaveURL(/\/chart\/NSE\/SBIN\?trip=7$/)
    await expect(page.getByTestId('phone-chart')).toBeVisible()
    await expect(page.getByRole('button', { name: '1m', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect.poll(() => caught.history.at(-1)).toBe('1m')
    await page.clock.runFor(1_000)
    await expect(page.getByTestId('trip-notice')).toHaveCount(0)
    expect(caught.journal.filter((op) => op === '/trips/7').length).toBeGreaterThanOrEqual(1)
    await noSidewaysScroll(page)
    await shot(page, 'trade-on-minutes')
  })

  test('a trade older than the minute candles says so and moves to daily ones', async ({ page }) => {
    await serve(page)
    await open(page, '/chart/NSE/SBIN?trip=8')
    const note = page.getByTestId('trip-notice')
    await expect(note).toHaveText(
      'This trade is older than the chart’s history at this interval. Daily candles are shown instead.'
    )
    await expect.poll(() => caught.history.slice(-2)).toEqual(['1m', 'D'])
    await expect(page.getByRole('button', { name: '1D', exact: true })).toHaveAttribute('aria-pressed', 'true')
    const dismiss = page.getByRole('button', { name: 'Dismiss this note' })
    const box = await dismiss.boundingBox()
    expect(box && box.width >= 47.5 && box.height >= 47.5).toBe(true)
    await noSidewaysScroll(page)
    await shot(page, 'old-trade-on-days')
    await dismiss.click()
    await expect(note).toHaveCount(0)
  })

  test('with the journal off the chart opens as always and says why nothing is marked', async ({ page }) => {
    await serve(page, { journalOff: true })
    await open(page, '/chart/NSE/SBIN?trip=7')
    await expect(page.getByTestId('trip-notice')).toHaveText(
      'The trade’s markers could not be shown. The journal is switched off on the server.'
    )
    await expect.poll(() => caught.history.length).toBeGreaterThan(0)
    expect(caught.history).not.toContain('1m')
    await shot(page, 'journal-off')
  })

  // Review v0.15.0, M1: an exit made while the chart is open is marked without leaving it.
  test('an exit made while the chart is open gets its S when the order update arrives', async ({ page }) => {
    await serve(page)
    await open(page, '/chart/NSE/SBIN?trip=9')
    await expect.poll(() => caught.history.at(-1)).toBe('1m')
    await page.clock.runFor(1_000)
    await expect.poll(() => inked(page, SELL_INK)).toBe(0)
    expect(await inked(page, BUY_INK)).toBeGreaterThan(20)
    const reads = () => caught.journal.filter((op) => op === '/trips/9').length
    const before = reads()
    // Nothing is read on a timer: ten minutes pass with no order and no read.
    await page.clock.runFor(10 * 60_000)
    expect(reads()).toBe(before)
    exited9 = true
    await push('order_update', { symbol: 'INFY', exchange: 'NSE', order_status: 'complete' })
    await page.clock.runFor(4_000)
    expect(reads(), 'another instrument’s order reads nothing').toBe(before)
    await push('order_update', { symbol: 'SBIN', exchange: 'NSE', action: 'SELL', order_status: 'complete' })
    await page.clock.runFor(4_000)
    await expect.poll(reads).toBe(before + 1)
    // The page's clock is the test's: the chart paints on its next frame.
    await expect
      .poll(async () => {
        await page.clock.runFor(200)
        return inked(page, SELL_INK)
      })
      .toBeGreaterThan(20)
    await expect(page.getByTestId('trip-notice')).toHaveCount(0)
    await shot(page, 'exit-marked-while-open')
  })

  // Review v0.15.0, L4: on 1-minute candles at 360 px a mark is a tag of a readable size with
  // its letter alone, and a tap on it opens the fill in full.
  test('a mark is a readable tag on minute candles, and a tap on it opens its fill', async ({ page }) => {
    await serve(page)
    await open(page, '/chart/NSE/SBIN?trip=7')
    await expect.poll(() => caught.history.at(-1)).toBe('1m')
    await page.clock.runFor(1_000)
    const tag = await expect
      .poll(async () => (await tagBox(page, BUY_INK)) !== null)
      .toBe(true)
      .then(() => tagBox(page, BUY_INK))
    // A plate of the letter: at least 12 px each way, and never as wide as the price would be.
    expect(tag && tag.width >= 12 && tag.height >= 12, JSON.stringify(tag)).toBe(true)
    expect(tag && tag.width < 40, JSON.stringify(tag)).toBe(true)
    if (!tag) return
    // A finger beside the tag still finds it.
    await page.mouse.click(tag.x + tag.width / 2 + 12, tag.y + tag.height / 2 + 10)
    await page.clock.runFor(600)
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByText('Entry: bought at this candle')).toBeVisible()
    await expect(sheet.getByTestId('trip-mark-fills')).toHaveText('10:02:05Bought10 at 803.05')
    await noSidewaysScroll(page)
    await shot(page, 'mark-fill-sheet')
  })

  // Review v0.15.0, L6: the same instrument opened without the trade loses the marks.
  test('the marks go when the chart is opened again without the trade', async ({ page }) => {
    await serve(page)
    await open(page, '/chart/NSE/SBIN?trip=7')
    await expect.poll(() => caught.history.at(-1)).toBe('1m')
    await page.clock.runFor(1_000)
    await expect.poll(() => inked(page, BUY_INK)).toBeGreaterThan(20)
    await page.evaluate(() => {
      window.history.pushState(window.history.state, '', '/chart/NSE/SBIN')
      window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }))
    })
    await expect(page).toHaveURL(/\/chart\/NSE\/SBIN$/)
    await page.clock.runFor(1_000)
    await expect.poll(() => inked(page, BUY_INK)).toBe(0)
    await expect.poll(() => inked(page, SELL_INK)).toBe(0)
  })
})

test.describe('the charges line in the order ticket (M5.10)', () => {
  test('reads the estimate, shows the parts on a tap, and sends nothing', async ({ page }) => {
    await serve(page)
    await open(page, '/chart/NSE/SBIN')
    const buy = page.getByRole('button', { name: 'BUY', exact: true })
    await expect(buy).toBeEnabled()
    await buy.click()
    const ticket = page.getByRole('dialog')
    await expect(ticket).toBeVisible()
    await page.clock.runFor(600)
    const line = ticket.getByTestId('charges-line')
    await expect(line).toContainText('Charges est. ₹44.94')
    expect(caught.journal.some((op) => op.startsWith('/charges/estimate?'))).toBe(true)
    const asked = new URLSearchParams(caught.journal.find((op) => op.startsWith('/charges/estimate?'))?.split('?')[1])
    expect(Object.fromEntries(asked)).toMatchObject({ exchange: 'NSE', symbol: 'SBIN', action: 'BUY', quantity: '1' })
    await line.getByRole('button').click()
    // Opened at the foot of the ticket's scrolling body, the parts are brought into view.
    await expect(line.getByRole('list', { name: 'Estimated charges, part by part' })).toBeInViewport()
    await expect(line).toContainText('An estimate from the journal’s rate card')
    const parts = await line.getByRole('button').boundingBox()
    expect(parts && parts.height >= 47.5).toBe(true)
    // The Place button and the order line beside it are where they were.
    await expect(ticket.getByTestId('ticket-summary')).toBeVisible()
    await expect(ticket.getByRole('button', { name: /^Place BUY Order$/ })).toBeVisible()
    await noSidewaysScroll(page)
    await shot(page, 'ticket-charges-parts')
  })

  test('says "not read" with the journal off, never ₹0', async ({ page }) => {
    await serve(page, { journalOff: true })
    await open(page, '/chart/NSE/SBIN')
    const buy = page.getByRole('button', { name: 'BUY', exact: true })
    await expect(buy).toBeEnabled()
    await buy.click()
    await page.clock.runFor(600)
    const line = page.getByRole('dialog').getByTestId('charges-line')
    await expect(line).toHaveText('Charges est. not read')
    await line.scrollIntoViewIfNeeded()
    await shot(page, 'ticket-charges-not-read')
  })
})
