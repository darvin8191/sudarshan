/**
 * The phone chart at 360 x 800, and on its side at 800 x 360 (M4.15, C14),
 * in a real browser with the real chart engine. Nothing reaches a server:
 * every REST answer is a fixture here, and OpenAlgo's market-data proxy is
 * played from frames built with the tick tape's builders
 * (`src/test/tickTape.ts`), all synthesised, none recorded. The page's clock
 * is Tuesday 20 October 2026 in India, a trading day.
 *
 * Each state of the screen (open, closed, reconnecting, lapsed login, and the
 * sheets) asserts what the design asks of every phone screen: no sideways
 * page scroll; every control at least 48 x 48 CSS px; the freshness words for
 * the state, and "Real-time" never without ticks; up and down told by a sign
 * or an arrow and not by colour alone, and text that keeps its contrast. A
 * screenshot of each is kept for the owner's eye.
 *
 * The cost run plays ticks every 250 ms into as many studies as the phone
 * chart takes (`MAX_STUDIES`), the costliest of them, two in panes of their
 * own, with the CPU throttled four times, upright and on its side: no long
 * task over 50 ms after load, and the frame interval's p95 under 34 ms. A
 * tripwire, not the truth: the truth is the owner's phone. It failed at five,
 * which is why the cap is three. On a busy machine it fails with no studies
 * at all; SD_COST_STUDIES=0 runs that baseline to compare.
 *
 * Chromium only (touch and throttling go through its DevTools protocol):
 *   npx playwright test e2e/sd-chart.spec.ts --project=chromium
 * Measure the cost run on a production build, not the development server,
 * which draws every component twice and checks as it goes: `npm run build`,
 * then `npx vite preview --port 5173`, which Playwright then uses as it is.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, type Locator, type Page, test, type WebSocketRoute } from '@playwright/test'
import { MAX_STUDIES } from '../src/sudarshan/chart/phoneStudies'
import { depthFrame } from '../src/test/tickTape'

test.skip(({ browserName }) => browserName !== 'chromium', 'touch and throttling are Chromium only')

/** Epoch milliseconds for a time of day in India on Tuesday 20 October 2026. */
const ist = (h: number, m: number, s = 0, day = 20) =>
  Date.UTC(2026, 9, day, h, m, 0) + Math.round(s * 1000) - 19_800_000

const SYMBOL = 'SBIN'
const EXCHANGE = 'NSE'

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

const TRADING_DAYS = [13, 14, 15, 16, 19, 20]

/** History as the exchange would hold it up to `untilMs`: five sessions and today. */
function history(interval: string, untilMs: number) {
  const step = { '1m': 60, '3m': 180, '5m': 300, '15m': 900, '1h': 3600 }[interval]
  const random = walk(812)
  let price = 812.4
  const rows: Array<Record<string, number>> = []
  const push = (time: number) => {
    const open = price
    const move = (random() - 0.5) * 2.4
    const close = Math.round((open + move) * 20) / 20
    const high = Math.max(open, close) + Math.round(random() * 0.8 * 20) / 20
    const low = Math.min(open, close) - Math.round(random() * 0.8 * 20) / 20
    price = close
    rows.push({
      timestamp: time,
      open,
      high,
      low,
      close,
      volume: 20_000 + Math.round(random() * 60_000),
    })
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
      if (t * 1000 > untilMs) return rows
      push(t)
    }
  return rows
}

const json = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

interface Fixture {
  /** The page's clock when it opens. */
  now: number
  /** Today's Upstox login has lapsed. */
  lapsed?: boolean
  /**
   * `fake` (the default): Playwright's clock, which the test runs. `shifted`:
   * only the date is moved to `now`, and timers, frames and paints run in real
   * time, as the cost run needs.
   */
  clock?: 'fake' | 'shifted'
  /** The books and alerts the chart's lines are read from (C5); empty books when left out. */
  books?: { positions: unknown[]; orders: unknown[]; alerts?: unknown[] }
  /** Analyze mode is on: the books are the sandbox's. */
  analyze?: boolean
}

/** A request an action of a chart line made: its path and what it carried. */
interface Sent {
  path: string
  body: Record<string, unknown>
}

/** The page's date moved to `target`, with every timer left real. */
function shiftDate(target: number) {
  const RealDate = Date
  const delta = target - RealDate.now()
  class ShiftedDate extends RealDate {
    constructor(...args: unknown[]) {
      super(...((args.length ? args : [RealDate.now() + delta]) as [number]))
    }
    static now() {
      return RealDate.now() + delta
    }
  }
  globalThis.Date = ShiftedDate as DateConstructor
}

/** Every request the phone chart makes, answered here; the proxy's socket handed back. */
async function serve(
  page: Page,
  fixture: Fixture
): Promise<{ proxy: () => WebSocketRoute; sockets: () => number; sent: Sent[] }> {
  const sockets: WebSocketRoute[] = []
  const sent: Sent[] = []
  // The pages' own exit, cancel and modify calls: answered here, never sent anywhere (C6).
  for (const path of ['close_position', 'cancel_order', 'modify_order'])
    await page.route(`**/${path}`, (route) => {
      sent.push({ path, body: route.request().postDataJSON() as Record<string, unknown> })
      return route.fulfill(json({ status: 'success', orderid: 'X9', data: { orderid: 'X9' } }))
    })
  await page.route('**/sudarshan/alerts/api/state', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: { enabled: true, alerts: fixture.books?.alerts ?? [], groups: [] },
      })
    )
  )
  if (fixture.clock === 'shifted') await page.addInitScript(shiftDate, fixture.now)
  else await page.clock.install({ time: fixture.now })
  // The app's own push connection (order updates), long-polling: a handshake,
  // the namespace joined, then a poll the server simply holds.
  let polls = 0
  await page.route('**/socket.io/**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'POST') return route.fulfill({ body: 'ok' })
    if (!url.searchParams.get('sid'))
      return route.fulfill({
        body: `0${JSON.stringify({ sid: 's1', upgrades: [], pingInterval: 600_000, pingTimeout: 600_000, maxPayload: 1_000_000 })}`,
      })
    if (polls++ === 0) return route.fulfill({ body: '40{"sid":"n1"}' })
    // Held, as a server holds a poll with nothing to say.
  })
  await page.route('**/auth/session-status*', (route) =>
    route.fulfill(
      json({
        status: 'success',
        authenticated: true,
        logged_in: true,
        user: 'owner',
        broker: 'upstox',
        api_key: 'test-key',
        broker_session_expired: fixture.lapsed === true,
        active_sessions: 1,
      })
    )
  )
  await page.route('**/auth/csrf-token', (route) => route.fulfill(json({ csrf_token: 'token' })))
  await page.route('**/auth/analyzer-mode', (route) =>
    route.fulfill(
      json({
        status: 'success',
        data: {
          analyze_mode: fixture.analyze === true,
          mode: fixture.analyze ? 'analyze' : 'live',
        },
      })
    )
  )
  await page.route('**/api/broker/capabilities', (route) =>
    route.fulfill(json({ status: 'success', data: { broker_type: 'stock', broker_name: 'upstox' } }))
  )
  await page.route('**/api/websocket/apikey', (route) =>
    route.fulfill(json({ status: 'success', api_key: 'test-key' }))
  )
  // A host that resolves nowhere: the socket is answered here and never leaves the browser.
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
    route.fulfill(
      json({ status: 'success', data: { day: '2026-10-20', stops_at: null, closing: null } })
    )
  )
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/', '')
    const body = (route.request().postDataJSON() ?? {}) as Record<string, string>
    switch (path) {
      case 'positionbook':
        return route.fulfill(json({ status: 'success', data: fixture.books?.positions ?? [] }))
      case 'orderbook':
        return route.fulfill(
          json({ status: 'success', data: { orders: fixture.books?.orders ?? [], statistics: {} } })
        )
      case 'symbol':
        return route.fulfill(
          json({
            status: 'success',
            data: { name: 'STATE BANK OF INDIA', lotsize: 1, tick_size: 0.05 },
          })
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
      case 'history': {
        const rows = history(body.interval, fixture.now)
        // A contract's history carries its open interest.
        const oi = body.exchange === 'NFO'
        return route.fulfill(
          json({ status: 'success', data: oi ? rows.map((r, i) => ({ ...r, oi: 1_100_000 + i * 650 })) : rows })
        )
      }
      case 'quotes':
        // The last trade the history holds at the page's time, as Upstox's quote would say.
        return route.fulfill(
          json({
            status: 'success',
            data: { ltp: Number(history('5m', fixture.now).at(-1)?.close), prev_close: PREV_CLOSE, volume: 4_210_000 },
          })
        )
      case 'depth':
        // The order ticket's own snapshot of the book.
        return route.fulfill(
          json({
            status: 'success',
            data: {
              ltp: LAST,
              prev_close: PREV_CLOSE,
              bids: [{ price: LAST - 0.05, quantity: 1_240 }],
              asks: [{ price: LAST, quantity: 860 }],
            },
          })
        )
      default:
        return route.fulfill(json({ status: 'success', data: [] }))
    }
  })
  await page.routeWebSocket(/\/proxy$/, (ws) => {
    sockets.push(ws)
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw)) as Record<string, unknown>
      if (message.action === 'authenticate') ws.send(JSON.stringify({ type: 'auth', status: 'success' }))
      else if (message.action === 'ping')
        // The server's clock agrees with the page's.
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
  return {
    proxy: () => {
      const socket = sockets.at(-1)
      if (!socket) throw new Error('the page has not opened the proxy socket')
      return socket
    },
    sockets: () => sockets.length,
    sent,
  }
}

/** Where the five-minute history leaves the price when the page opens, and yesterday's close. */
const LAST = Number(history('5m', ist(10, 0, 30)).at(-1)?.close)
const PREV_CLOSE = Number(history('D', ist(10, 0, 30)).at(-2)?.close)

interface Instrument {
  symbol: string
  exchange: string
  oi?: number
}

/** One frame from the exchange at `at` (the page's milliseconds), the i-th of a run. */
function frame(i: number, at: number, instrument: Instrument) {
  const price = Math.round((LAST + [0.05, -0.1, 0.15][i % 3] * Math.min(i, 9)) * 100) / 100
  return JSON.stringify(
    depthFrame(instrument.symbol, instrument.exchange, {
      ltp: price,
      at,
      tradeAt: at - 120,
      volume: 4_210_000 + i * 150,
      ltq: 150,
      prevClose: PREV_CLOSE,
      bid: { price: Math.round((price - 0.05) * 100) / 100, quantity: 1_240 },
      ask: { price, quantity: 860 },
      ...(instrument.oi ? { oi: instrument.oi + i * 650 } : {}),
    })
  )
}

const STOCK: Instrument = { symbol: SYMBOL, exchange: EXCHANGE }

/** Ticks from the exchange, one every `every` ms of the page's fake clock. */
async function tick(
  page: Page,
  proxy: WebSocketRoute,
  from: number,
  count: number,
  every = 1000,
  instrument = STOCK
) {
  for (let i = 0; i < count; i++) {
    proxy.send(frame(i, from + i * every, instrument))
    await page.clock.runFor(every)
  }
}

/** The price header, or the strip on its side. */
const price = (page: Page) => page.getByRole('region', { name: 'Price' })

/** Open the chart and hide the development build's query devtools, which are not the app. */
async function open(page: Page, path = `/chart/${EXCHANGE}/${SYMBOL}`) {
  await page.goto(path)
  await expect(page.getByTestId('phone-chart')).toBeVisible()
  await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' })
}

/** What every phone screen promises, checked in one state of the chart. */
async function checkScreen(page: Page) {
  const width = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(width.scroll, 'no sideways page scroll').toBeLessThanOrEqual(width.client)
  const small = await page.evaluate(() =>
    [...document.querySelectorAll('button, a[href], [role="switch"], input, select')]
      .filter((el) => {
        const box = el.getBoundingClientRect()
        const style = getComputedStyle(el)
        if (box.width === 0 && box.height === 0) return false
        if (style.visibility === 'hidden' || style.display === 'none') return false
        if (el.closest('.tsqd-parent-container')) return false
        // A control inside a 48 px label row counts by the row it sits in.
        const row = el.closest('label')?.getBoundingClientRect()
        const w = Math.max(box.width, row?.width ?? 0)
        const h = Math.max(box.height, row?.height ?? 0)
        return w < 47.5 || h < 47.5
      })
      .map((el) => `${el.tagName} "${(el.getAttribute('aria-label') ?? el.textContent ?? '').trim()}"`)
  )
  expect(small, 'every control at least 48 x 48').toEqual([])
  // Up and down are told by a sign or an arrow as well as by colour.
  const bare = await page.evaluate(() =>
    [...document.querySelectorAll('[class*="var(--up)"], [class*="var(--down)"]')]
      .filter((el) => /\d/.test(el.textContent ?? ''))
      .filter((el) => !/[+−]/.test(el.textContent ?? '') && !el.querySelector('svg[data-direction]'))
      .map((el) => (el.textContent ?? '').trim())
  )
  expect(bare, 'an up or down figure with neither a sign nor an arrow').toEqual([])
  // Text keeps its contrast in every state, a grey price and a quiet book included.
  const contrast = await new AxeBuilder({ page })
    .withRules(['color-contrast', 'link-in-text-block'])
    .exclude('.tsqd-parent-container')
    .analyze()
  expect(
    contrast.violations.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}`)),
    'text that loses its contrast, or a link told by colour alone'
  ).toEqual([])
}

/** The chart's freshness words, and "Real-time" nowhere unless ticks are arriving. */
async function checkFresh(page: Page, words: string | RegExp, ticking = false) {
  await expect(page.getByTestId('phone-chart').locator('[data-feed]').first()).toHaveText(words)
  if (!ticking) await expect(page.getByText('Real-time', { exact: true })).toHaveCount(0)
}

/**
 * What just opened, past the half second it takes no tap (`openGuard.ts`) and
 * looks it. The page draws it on the browser's own clock, so the test's clock
 * runs on only once it is there.
 */
async function settled(page: Page, opened: Locator) {
  await expect(opened).toBeVisible()
  await page.clock.runFor(600)
  await expect(page.locator('[data-settling]')).toHaveCount(0)
}

/** Press and hold a finger at a point of the page until the chart reads the candle under it. */
async function holdAt(page: Page, x: number, y: number) {
  const touch = await page.context().newCDPSession(page)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] })
  await page.clock.runFor(600)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await touch.detach()
}

/** As many studies as the phone chart takes (three), two in panes of their own. */
const FULL = [
  { id: 'ema', settings: { length: 21 } },
  { id: 'rsi', settings: {} },
  { id: 'macd', settings: {} },
].slice(0, MAX_STUDIES)

/** What the phone keeps, put in its storage before the page loads. */
async function keep(page: Page, values: Record<string, unknown>) {
  await page.addInitScript((entries) => {
    for (const [key, value] of entries) localStorage.setItem(key, value)
  }, Object.entries(values).map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]))
}

test.use({
  viewport: { width: 360, height: 800 },
  deviceScaleFactor: 3,
  hasTouch: true,
  isMobile: true,
  // A production build registers a service worker, which would answer requests before the fixtures.
  serviceWorkers: 'block',
  // Fades run on the browser's own clock, not the test's: settled at once, as the app does for
  // a phone set to reduce motion, so a check never reads a sheet half faded in.
  contextOptions: { reducedMotion: 'reduce' },
})

test.describe('the phone chart at 360 x 800', () => {
  test('in a market that is open, ticks arriving; a held price offers Buy and Sell at it', async ({ page }, info) => {
    const { proxy } = await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    await expect(page.getByRole('button', { name: 'BUY' })).toBeEnabled()
    // Before the first tick, no word says the prices are live.
    await expect(page.getByText('Real-time', { exact: true })).toHaveCount(0)
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 6)
    await expect(price(page).locator('[data-feed]')).toHaveText('Real-time')
    await expect(page.getByTestId('chart-change')).toHaveText(/^[+−]₹[\d,.]+ \([+−][\d.]+%\)$/)
    await expect(page.getByTestId('chart-change').locator('[data-direction]')).toHaveCount(1)
    await page.screenshot({ path: info.outputPath('open.png') })
    await checkScreen(page)
    // Hold to read a candle (C10): Buy and Sell at the price under the finger.
    const surface = (await page.getByTestId('chart-surface').boundingBox()) as DOMRect
    await holdAt(page, surface.x + surface.width * 0.5, surface.y + surface.height * 0.55)
    await settled(page, page.getByRole('button', { name: /^Sell at [\d,]+\.\d{2}$/ }))
    await page.screenshot({ path: info.outputPath('held.png') })
    await checkScreen(page)
  })

  test('in a closed market: closed, when it opens, and the book as the session left it', async ({ page }, info) => {
    await serve(page, { now: ist(16, 30) })
    await open(page)
    await expect(page.getByRole('button', { name: 'BUY' })).toBeEnabled()
    await page.clock.runFor(1500)
    await checkFresh(page, 'Market closed')
    await expect(page.getByTestId('chart-last-tick')).toHaveText(/^Opens /)
    await page.screenshot({ path: info.outputPath('closed.png') })
    await checkScreen(page)
  })

  test('reconnecting when the stream drops, then Real-time again with the next ticks', async ({ page }, info) => {
    const { proxy, sockets } = await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 3)
    await expect(price(page).locator('[data-feed]')).toHaveText('Real-time')
    await proxy().close()
    await page.clock.runFor(200)
    await checkFresh(page, /^Reconnecting/)
    await expect(page.getByTestId('depth-strip')).toHaveAttribute('aria-label', /Reconnecting: the book is not current/)
    await page.screenshot({ path: info.outputPath('reconnecting.png') })
    await checkScreen(page)
    // The socket is opened again on its own, and the ticks bring the word back.
    for (let i = 0; i < 30 && sockets() < 2; i++) await page.clock.runFor(500)
    expect(sockets(), 'a new socket after the drop').toBe(2)
    await page.clock.runFor(500)
    await tick(page, proxy(), await page.evaluate(() => Date.now()), 3)
    await expect(price(page).locator('[data-feed]')).toHaveText('Real-time')
  })

  test("today's Upstox login has lapsed: says so, with no chart and no live word", async ({ page }, info) => {
    await serve(page, { now: ist(10, 0, 30), lapsed: true })
    await page.goto(`/chart/${EXCHANGE}/${SYMBOL}`)
    await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' })
    await expect(page.getByText(/Upstox is not logged in today, so there are no prices/)).toBeVisible()
    await expect(page.getByTestId('chart-surface')).toHaveCount(0)
    await expect(page.getByText('Real-time', { exact: true })).toHaveCount(0)
    await page.screenshot({ path: info.outputPath('lapsed.png') })
    await checkScreen(page)
  })

  test('with the most indicators it takes, two in their own panes, the sheet, search and settings', async ({ page }, info) => {
    await keep(page, { 'sudarshan-chart-studies': FULL })
    const { proxy } = await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    await expect(page.getByRole('button', { name: `Indicators ${FULL.length} on the chart` })).toBeVisible()
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 4)
    await expect(page.getByTestId('study-legend')).toContainText('RSI')
    await page.screenshot({ path: info.outputPath('full.png') })
    await checkScreen(page)
    await page.getByRole('button', { name: `Indicators ${FULL.length} on the chart` }).click()
    await settled(page, page.getByRole('dialog', { name: 'Indicators' }))
    await page.screenshot({ path: info.outputPath('sheet.png') })
    await checkScreen(page)
    await page.getByRole('searchbox').fill('mov')
    await page.screenshot({ path: info.outputPath('search.png') })
    await checkScreen(page)
    await page.getByRole('button', { name: 'Settings for EMA 21 close' }).click()
    const form = page.getByRole('dialog', { name: 'EMA' })
    await settled(page, form)
    await expect(form.getByRole('textbox', { name: 'Length' })).toHaveValue('21')
    await page.screenshot({ path: info.outputPath('settings.png') })
    await checkScreen(page)
  })

  test('a contract: open interest and its change, and the book strip and sheet', async ({ page }, info) => {
    const contract = { symbol: 'NIFTY27OCT2625900CE', exchange: 'NFO', oi: 1_206_500 }
    const { proxy } = await serve(page, { now: ist(10, 0, 30) })
    await open(page, `/chart/${contract.exchange}/${contract.symbol}`)
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 4, 1000, contract)
    await expect(page.getByTestId('chart-oi')).toContainText('since yesterday')
    await expect(page.getByTestId('depth-strip')).toContainText('Bid')
    await page.screenshot({ path: info.outputPath('contract.png') })
    await checkScreen(page)
    await page.getByTestId('depth-strip').click()
    await settled(page, page.getByRole('dialog', { name: 'Market depth' }))
    await page.screenshot({ path: info.outputPath('depth.png') })
    await checkScreen(page)
  })

  test('keeping the screen on where the phone will not: the switch goes off and says why', async ({ page }, info) => {
    // A browser with the wake lock that refuses it, as one in battery saver does.
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'wakeLock', {
        configurable: true,
        value: { request: () => Promise.reject(new DOMException('refused', 'NotAllowedError')) },
      })
    })
    await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    const toggle = page.getByRole('switch', { name: 'Keep screen on' })
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await expect(page.getByText('The phone would not keep the screen on.')).toBeVisible()
    await page.screenshot({ path: info.outputPath('wake-refused.png') })
    await checkScreen(page)
  })
})

test.describe('the phone chart on its side, 800 x 360', () => {
  test.use({ viewport: { width: 800, height: 360 } })

  test('one strip, the chart the rest; Buy at a held price opens the ticket at it', async ({ page }, info) => {
    const { proxy } = await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    const strip = page.getByTestId('chart-strip')
    await expect(strip.getByRole('button', { name: 'BUY' })).toBeEnabled()
    await expect(page.getByText('Real-time', { exact: true })).toHaveCount(0)
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 6)
    await expect(strip.locator('[data-feed]')).toHaveText('Real-time')
    await expect(strip.getByTestId('chart-change')).toHaveText(/^[+−]₹[\d,.]+ \([+−][\d.]+%\)$/)
    // The strip is the 48 dp the design gives it, and the chart takes the rest of the height.
    const stripBox = await strip.boundingBox()
    const surfaceBox = await page.getByTestId('chart-surface').boundingBox()
    expect(stripBox?.height).toBe(48)
    expect(Math.round((surfaceBox?.height ?? 0) + (stripBox?.height ?? 0))).toBe(360)
    await page.screenshot({ path: info.outputPath('sideways.png') })
    await checkScreen(page)

    const surface = surfaceBox as { x: number; y: number; width: number; height: number }
    // Low on the candles, clear of where the reading opens at the top.
    await holdAt(page, surface.x + surface.width * 0.6, surface.y + surface.height * 0.6)
    const buy = page.getByRole('button', { name: /^Buy at [\d,]+\.\d{2}$/ })
    await expect(buy).toBeVisible()
    const held = (await buy.textContent())?.replace('Buy at ', '') ?? ''
    await expect(page.getByTestId('chart-held-price')).toHaveText(held)
    // For its first half second the reading takes no tap: the tail of the hold is not a press.
    await settled(page, buy)
    await page.screenshot({ path: info.outputPath('sideways-held.png') })
    await checkScreen(page)
    await buy.click()
    // The app's own ticket: a limit at the price held, on the product the ticket picks.
    const ticket = page.getByRole('dialog', { name: 'Place Order - BUY SBIN' })
    await settled(page, ticket)
    await expect(ticket.getByRole('combobox').nth(1)).toHaveText('Limit')
    await expect(ticket.getByRole('combobox').nth(2)).toHaveText('CNC')
    await expect(ticket.getByRole('spinbutton', { name: 'Quantity' })).toBeVisible()
    await expect(ticket.getByRole('spinbutton', { name: 'Price', exact: true })).toHaveValue(
      String(Number(held.replaceAll(',', '')))
    )
    await page.screenshot({ path: info.outputPath('sideways-ticket.png') })
  })

  test('with the most indicators it takes, two in their own panes', async ({ page }, info) => {
    await keep(page, { 'sudarshan-chart-studies': FULL })
    const { proxy } = await serve(page, { now: ist(10, 0, 30) })
    await open(page)
    await expect(page.getByRole('button', { name: `Indicators, ${FULL.length} on the chart` })).toBeVisible()
    await page.clock.runFor(500)
    await tick(page, proxy(), ist(10, 0, 31), 4)
    await expect(page.getByTestId('study-legend')).toContainText('MACD')
    await page.screenshot({ path: info.outputPath('sideways-full.png') })
    await checkScreen(page)
  })
})

/**
 * The cost run's studies: the costliest the common list offers, in the order
 * of their cost per tick (measured: VWAP, RSI and MACD, then Supertrend and
 * EMA), as many as the phone chart takes, or the first SD_COST_STUDIES of
 * them (0 is the same run with none, the baseline to compare with).
 */
const COSTLIEST = ['vwap', 'rsi', 'macd', 'supertrend', 'ema'].map((id) => ({ id, settings: {} }))
const COST_STUDIES = COSTLIEST.slice(0, Number(process.env.SD_COST_STUDIES ?? MAX_STUDIES))

/** The 95th percentile of a list of numbers. */
const p95 = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? 0
}


/**
 * Lines and their sheet (M4.7, C5 and C6): a long position, its stop below and a price alert
 * above, read from fixtures. Every exit, cancel and modify the sheet sends is caught by `serve`
 * and counted: nothing leaves the browser.
 */
const HELD = {
  symbol: SYMBOL,
  exchange: EXCHANGE,
  product: 'MIS',
  quantity: 10,
  average_price: Math.round((LAST - 2) * 20) / 20,
  ltp: LAST,
  pnl: 20,
  pnlpercent: 0.25,
}
const STOP = {
  symbol: SYMBOL,
  exchange: EXCHANGE,
  action: 'SELL',
  quantity: 10,
  price: Math.round((LAST - 5.5) * 20) / 20,
  trigger_price: Math.round((LAST - 5) * 20) / 20,
  pricetype: 'SL',
  product: 'MIS',
  orderid: '261020000000017',
  order_status: 'trigger pending',
  timestamp: '2026-10-20 09:41:51',
}
const WATCH = {
  id: 7,
  version: 2,
  status: 'active',
  status_reason: '',
  kind: 'price',
  symbol: SYMBOL,
  exchange: EXCHANGE,
  instrument_key: 'NSE_EQ|INE062A01020',
  tick_size: '0.05',
  expiry: null,
  params: { op: 'at_or_above', level: String(Math.round((LAST + 4) * 20) / 20) },
  frequency: 'once',
  candle_min: null,
  cooldown_s: 0,
  ends_at: null,
  note: '',
  channels: null,
  created_at: 0,
  updated_at: 0,
  words: { condition: '', meta: '', checks_at: null },
  group_id: null,
  check: null,
  fired_today: 0,
  auction: null,
}
const BOOKS = { positions: [HELD], orders: [STOP], alerts: [WATCH] }

const money = /[+−]₹[\d,]+\.\d{2}/
const label = (page: Page, words: RegExp) =>
  page.getByTestId('chart-lines').getByRole('button', { name: words })

/**
 * Tap the label of the line with these words and wait for its sheet. Lines nearer than a
 * touch target share one, which opens a list first: the line is then picked from it.
 */
async function openLine(page: Page, words: RegExp) {
  await label(page, words).click()
  const sheet = page.getByRole('dialog', { name: SYMBOL })
  await settled(page, sheet)
  const pick = sheet.getByRole('list', { name: 'Lines near this price' })
  if (await pick.count()) {
    await pick.getByRole('button', { name: words }).click()
    await expect(pick).toHaveCount(0)
    await page.clock.runFor(600)
    await expect(page.locator('[data-settling]')).toHaveCount(0)
  }
  return sheet
}

for (const [layout, viewport] of [
  ['upright', { width: 360, height: 800 }],
  ['on its side', { width: 800, height: 360 }],
] as const)
  test.describe(`lines on the phone chart ${layout}, ${viewport.width} x ${viewport.height}`, () => {
    test.use({ viewport })

    test('a position, its stop and an alert as lines with words; Exit and Add from the position', async ({ page }, info) => {
      const { proxy, sent } = await serve(page, { now: ist(10, 0, 30), books: BOOKS })
      await open(page)
      await page.clock.runFor(500)
      await tick(page, proxy(), ist(10, 0, 31), 4)
      const lines = page.getByTestId('chart-lines')
      // Every line says its side or what it is in words, and each kind has its own mark.
      await expect(lines.locator('[data-line]')).toHaveCount(3)
      await expect(lines).toContainText(/Buy 10 @ [\d,]+\.\d{2}/)
      // The stop's trigger and limit are half a rupee apart: one label, the other counted on it.
      await expect(lines).toContainText(/SELL 10 SL trigger [\d,]+\.\d{2}\+1/)
      await expect(lines).toContainText(/Alert ≥ [\d,]+\.\d{2}/)
      // The position's P&L has its sign and its arrow, and has moved off the book's with the ticks.
      const held = lines.locator('[data-line="position:MIS"]')
      await expect(held).toContainText(money)
      await expect(held.locator('svg[data-direction]')).toHaveCount(1)
      // A label stands on its line: higher price, higher on the screen.
      const y = async (id: string) =>
        ((await lines.locator(`[data-line="${id}"]`).boundingBox()) as DOMRect).y
      expect(await y('alert:7')).toBeLessThan(await y('position:MIS'))
      expect(await y('position:MIS')).toBeLessThan(await y(`order:${STOP.orderid}:trigger`))
      await page.screenshot({ path: info.outputPath('lines.png') })
      await checkScreen(page)

      const sheet = await openLine(page, /Buy 10 @/)
      await expect(sheet.getByRole('button', { name: /^Exit/ })).toContainText('Sell 10 at market')
      await page.screenshot({ path: info.outputPath('position-sheet.png') })
      await checkScreen(page)
      await sheet.getByRole('button', { name: /^Exit/ }).click()
      const confirm = page.getByRole('dialog', { name: /^Exit .*: sell 10 at market\?$/ })
      await settled(page, confirm)
      await page.screenshot({ path: info.outputPath('exit-confirm.png') })
      await checkScreen(page)
      expect(sent, 'nothing is sent before the confirmation').toEqual([])
      await confirm.getByRole('button', { name: 'Exit' }).dblclick()
      await expect(sheet.getByRole('button', { name: /^Exit/ })).toContainText('Exit sent')
      await expect(sheet.getByRole('button', { name: /^Exit/ })).toBeDisabled()
      expect(sent).toEqual([
        { path: 'close_position', body: { symbol: SYMBOL, exchange: EXCHANGE, product: 'MIS' } },
      ])

      // Add: the app's own ticket, on the position's side and in its product.
      await sheet.getByRole('button', { name: /^Add/ }).click()
      const ticket = page.getByRole('dialog', { name: `Place Order - BUY ${SYMBOL}` })
      await settled(page, ticket)
      await expect(ticket.getByRole('combobox').nth(2)).toHaveText('MIS')
      expect(sent).toHaveLength(1)
    })

    test('Modify and Cancel from an order line, each sent once', async ({ page }, info) => {
      const { proxy, sent } = await serve(page, { now: ist(10, 0, 30), books: BOOKS })
      await open(page)
      await page.clock.runFor(500)
      await tick(page, proxy(), ist(10, 0, 31), 2)
      // The stop's trigger and limit are half a rupee apart: one target, and a list to pick from.
      await label(page, /SELL 10 SL trigger/).click()
      const sheet = page.getByRole('dialog', { name: SYMBOL })
      await settled(page, sheet)
      await page.screenshot({ path: info.outputPath('pick.png') })
      await checkScreen(page)
      await sheet.getByRole('button', { name: /SELL 10 SL limit/ }).click()
      await settled(page, sheet.getByRole('button', { name: 'Modify' }))
      await sheet.getByRole('button', { name: 'Modify' }).click()
      await settled(page, sheet.getByRole('spinbutton', { name: 'Price', exact: true }))
      await page.screenshot({ path: info.outputPath('modify.png') })
      await checkScreen(page)
      const lower = String(Math.round((STOP.price - 0.5) * 20) / 20)
      await sheet.getByRole('spinbutton', { name: 'Price', exact: true }).fill(lower)
      await sheet.getByRole('button', { name: 'Modify order' }).dblclick()
      await expect(sheet).toHaveCount(0)
      expect(sent).toEqual([
        {
          path: 'modify_order',
          body: {
            orderid: STOP.orderid,
            symbol: SYMBOL,
            exchange: EXCHANGE,
            action: 'SELL',
            product: 'MIS',
            pricetype: 'SL',
            quantity: 10,
            price: Number(lower),
            trigger_price: STOP.trigger_price,
          },
        },
      ])

      await openLine(page, /SELL 10 SL trigger/)
      await sheet.getByRole('button', { name: 'Cancel order' }).click()
      const confirm = page.getByRole('dialog', { name: 'Cancel this order?' })
      await settled(page, confirm)
      await expect(confirm).toContainText('The order stops working at the exchange.')
      await page.screenshot({ path: info.outputPath('cancel-confirm.png') })
      await checkScreen(page)
      await confirm.getByRole('button', { name: 'Cancel order' }).dblclick()
      await expect(sheet.getByRole('button', { name: 'Cancel sent' })).toBeDisabled()
      expect(sent.map((one) => [one.path, one.body.orderid])).toEqual([
        ['modify_order', STOP.orderid],
        ['cancel_order', STOP.orderid],
      ])
    })

    test('in Analyze mode the sheet says its orders go to the sandbox', async ({ page }, info) => {
      const { proxy } = await serve(page, { now: ist(10, 0, 30), books: BOOKS, analyze: true })
      await open(page)
      await page.clock.runFor(500)
      await tick(page, proxy(), ist(10, 0, 31), 2)
      const sheet = await openLine(page, /Buy 10 @/)
      await expect(sheet.getByRole('button', { name: /^Exit/ })).toContainText('Sell 10 in the sandbox')
      await expect(sheet.getByRole('button', { name: /^Add/ })).toContainText('in the sandbox')
      await page.screenshot({ path: info.outputPath('analyze-sheet.png') })
      await checkScreen(page)
      await sheet.getByRole('button', { name: /^Exit/ }).click()
      const confirm = page.getByRole('dialog', { name: /^Exit .*: sell 10 at market\?$/ })
      await settled(page, confirm)
      await expect(confirm).toContainText('Analyze mode is on, so nothing goes to Upstox.')
    })
  })


/** What the phone keeps of an instrument's drawings: each one's tool, as saved. */
const savedTools = (page: Page) =>
  page.evaluate((key) => {
    const doc = JSON.parse(localStorage.getItem(key) ?? 'null') as {
      drawings?: Array<{ tool: string }>
    } | null
    return (doc?.drawings ?? []).map((drawing) => drawing.tool)
  }, `sudarshan-chart-draw:${EXCHANGE}:${SYMBOL}`)

for (const [layout, viewport] of [
  ['upright', { width: 360, height: 800 }],
  ['on its side', { width: 800, height: 360 }],
] as const)
  test.describe(`drawing on the phone chart ${layout}, ${viewport.width} x ${viewport.height}`, () => {
    test.use({ viewport })

    test('a level by one tap, a trend line by two, Undo, and both kept for the instrument', async ({ page }, info) => {
      const { proxy } = await serve(page, { now: ist(10, 0, 30) })
      await open(page)
      await page.clock.runFor(500)
      await tick(page, proxy(), ist(10, 0, 31), 2)
      await page.getByRole('button', { name: 'Draw' }).click()
      const row = page.getByTestId('draw-row')
      await expect(row.getByRole('button')).toHaveCount(7)
      await page.screenshot({ path: info.outputPath('draw-row.png') })
      await checkScreen(page)

      const box = (await page.getByTestId('chart-surface').boundingBox()) as DOMRect
      const at = (fx: number, fy: number) => [box.x + box.width * fx, box.y + box.height * fy] as const
      const tap = async (fx: number, fy: number) => {
        await page.touchscreen.tap(...at(fx, fy))
        await page.clock.runFor(100)
      }
      await row.getByRole('button', { name: 'Horizontal line' }).click()
      await expect(row.getByRole('status')).toHaveText('Horizontal line: tap the chart to place it.')
      await tap(0.45, 0.5)
      await expect.poll(() => savedTools(page)).toEqual(['horizontal-line'])
      // Placed: the tool is let go, so the next touch does not draw another.
      await expect(row.getByRole('button', { name: 'Horizontal line' })).toHaveAttribute('aria-pressed', 'false')

      await row.getByRole('button', { name: 'Trend line' }).click()
      await expect(row.getByRole('status')).toHaveText('Trend line: tap where it starts, then where it ends.')
      await tap(0.25, 0.62)
      expect(await savedTools(page), 'one tap is half a trend line').toEqual(['horizontal-line'])
      await tap(0.6, 0.42)
      await expect.poll(() => savedTools(page)).toEqual(['horizontal-line', 'trend-line'])
      await page.screenshot({ path: info.outputPath('drawn.png') })
      await checkScreen(page)

      await page.getByRole('button', { name: 'Undo' }).click()
      await expect.poll(() => savedTools(page)).toEqual(['horizontal-line'])

      // A tap on the level selects it (C8): its ink, Lock and Delete, in a bar at the foot.
      const levelY = await page.evaluate((key) => {
        const doc = JSON.parse(localStorage.getItem(key) ?? '{}') as {
          drawings: Array<{ points: Array<{ price: number }> }>
        }
        return doc.drawings[0].points[0].price
      }, `sudarshan-chart-draw:${EXCHANGE}:${SYMBOL}`)
      expect(levelY).toBeGreaterThan(0)
      await tap(0.45, 0.5)
      const bar = page.getByTestId('drawing-bar')
      await expect(bar).toBeVisible()
      await page.screenshot({ path: info.outputPath('selected.png') })
      await checkScreen(page)
      await bar.getByRole('button', { name: 'Peacock, dashed' }).click()
      await expect(bar.getByRole('button', { name: 'Peacock, dashed' })).toHaveAttribute('aria-pressed', 'true')
      await bar.getByRole('button', { name: 'Lock where it is' }).click()
      await expect(bar).toHaveCount(0)
      // Locked, it no longer answers a tap; Unlock is the way back.
      await tap(0.45, 0.5)
      await expect(bar).toHaveCount(0)
      await page.getByRole('button', { name: 'Unlock 1 locked' }).click()
      await tap(0.45, 0.5)
      await expect(bar).toBeVisible()
      await bar.getByRole('button', { name: 'Delete this horizontal line' }).click()
      await expect.poll(() => savedTools(page)).toEqual([])
      await page.getByRole('button', { name: 'Undo' }).click()
      await expect.poll(() => savedTools(page)).toEqual(['horizontal-line'])
      await page.getByRole('button', { name: 'Done' }).click()
      await expect(row).toHaveCount(0)

      // Another instrument starts clean, and the level is there when this one is opened again.
      await page.goto(`/chart/${EXCHANGE}/${SYMBOL}`)
      await expect(page.getByTestId('phone-chart')).toBeVisible()
      await expect.poll(() => savedTools(page)).toEqual(['horizontal-line'])
    })
  })

for (const [layout, viewport] of [
  ['upright', { width: 360, height: 800 }],
  ['on its side', { width: 800, height: 360 }],
] as const) {
  test.describe(`the cost run ${layout}`, () => {
    test.use({ viewport })

    test(`${COST_STUDIES.length} studies on a one-minute chart, ticks every 250 ms, the CPU four times slower`, async ({ page }, info) => {
      test.setTimeout(120_000)
      // From 10:00:50, so a new candle opens during the run.
      await keep(page, { 'sudarshan-chart-studies': COST_STUDIES, 'sudarshan-chart-interval': '1m' })
      const { proxy } = await serve(page, { now: ist(10, 0, 50), clock: 'shifted' })
      await open(page)
      const shift = (await page.evaluate(() => Date.now())) - Date.now()
      const send = (i: number) => proxy().send(frame(i, Date.now() + shift, STOCK))
      for (let i = 0; i < 4; i++) {
        send(i)
        await page.waitForTimeout(250)
      }
      await expect(page.getByTestId('phone-chart').locator('[data-feed]').first()).toHaveText('Real-time')
      if (COST_STUDIES.length) await expect(page.getByTestId('study-legend')).toBeVisible()

      const cpu = await page.context().newCDPSession(page)
      await cpu.send('Emulation.setCPUThrottlingRate', { rate: 4 })
      // Settled first, then measured: every task over 50 ms, and every frame.
      await page.waitForTimeout(1000)
      await page.evaluate(() => {
        const w = window as unknown as { costLong: number[]; costFrames: number[]; costRun: boolean }
        w.costLong = []
        w.costFrames = []
        w.costRun = true
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) w.costLong.push(entry.duration)
        }).observe({ type: 'longtask' })
        const paint = (t: number) => {
          if (!w.costRun) return
          w.costFrames.push(t)
          requestAnimationFrame(paint)
        }
        requestAnimationFrame(paint)
      })
      for (let i = 4; i < 52; i++) {
        send(i)
        await page.waitForTimeout(250)
      }
      const run = await page.evaluate(() => {
        const w = window as unknown as { costLong: number[]; costFrames: number[]; costRun: boolean }
        w.costRun = false
        return { long: w.costLong, frames: w.costFrames }
      })
      await cpu.send('Emulation.setCPUThrottlingRate', { rate: 1 })
      await cpu.detach()
      const gaps = run.frames.slice(1).map((t, i) => t - run.frames[i])
      const result = {
        frames: run.frames.length,
        frameP95: Math.round(p95(gaps) * 10) / 10,
        longest: Math.round(Math.max(0, ...run.long)),
        longTasks: run.long.length,
      }
      info.annotations.push({ type: 'cost', description: JSON.stringify(result) })
      await page.screenshot({ path: info.outputPath(`cost-${layout.replaceAll(' ', '-')}.png`) })
      expect(run.frames.length, 'frames painted during the run').toBeGreaterThan(100)
      expect(result.longest, 'no long task over 50 ms after load').toBeLessThanOrEqual(50)
      expect(result.frameP95, "the frame interval's p95").toBeLessThan(34)
    })
  })
}
