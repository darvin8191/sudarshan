/**
 * SUDARSHAN O10 (screens design 8.3, 8.4): the options screen on a phone, 360 x 800 with touch, in
 * each state of its table (market open with ticks, market closed, the feed down, the Upstox login
 * lapsed), the strike sheet and the ticket it opens, the Straddle tab with its computed chart (O8,
 * C11), and the cost run on the chain and on the straddle. Every REST call is answered
 * here and the market-data socket plays a tape synthesised in the proxy's message shape: nothing
 * reaches Upstox, NSE or Telegram, no recorded market data is used and no order is sent anywhere.
 * A screenshot of each state is kept as a test artifact.
 *
 *   npx playwright test e2e/sd-options.spec.ts --project=chromium
 *
 * The cost run is a tripwire, not the truth (the truth is the owner's phone). It reads the main
 * thread's own CPU time for every task from a trace and scales it by four, so it gives the same
 * answer on a busy machine, where a slowed clock and wall time do not. It measures a production
 * build (`vite build`, then `vite preview`) and is skipped on the dev server, whose React is
 * several times slower.
 *
 * Each state asserts: no sideways scroll; every link, button and switch at least 48 x 48 CSS px;
 * the freshness words for the state, and no "Real-time" without ticks; every up or down figure
 * carries a sign or an arrow, not colour alone; and no colour-contrast finding from axe.
 */
import AxeBuilder from '@axe-core/playwright'
import { type Browser, expect, type Page, type TestInfo, test, type WebSocketRoute } from '@playwright/test'

test.use({ viewport: { width: 360, height: 800 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true })

const WS_PATH = '/__sd_market'
const IST_MS = 5.5 * 3_600_000
const EXPIRY = '2026-11-17'
const SPOT = 25_912.35
const STRIKES = Array.from({ length: 81 }, (_, i) => 23_900 + i * 50)
const FUTURE = 'NIFTY24NOV26FUT'
/** How much slower than this machine a mid-range phone is taken to be (design 2.5: "CPU throttled 4x"). */
const SLOWER = 4

/** Epoch ms of an IST wall time: ist('2026-11-17', '09:15'). */
function ist(day: string, hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return Date.parse(`${day}T00:00:00Z`) - IST_MS + (h * 60 + m) * 60_000
}

const symbolOf = (strike: number, side: 'CE' | 'PE') => `NIFTY17NOV26${strike}${side}`

/** A made-up leg: a premium that falls away from the money, open interest that varies by strike. */
function leg(strike: number, side: 'CE' | 'PE') {
  const intrinsic = Math.max(side === 'CE' ? SPOT - strike : strike - SPOT, 0)
  const ltp = Math.round((intrinsic + 6 + 1900 / (12 + Math.abs(strike - SPOT) / 9)) * 20) / 20
  const oi = 65 * (900 + ((strike / 50) % 9) * 5200 + (side === 'CE' ? 400 : 2100))
  return {
    symbol: symbolOf(strike, side),
    exchange: 'NFO',
    lot: 65,
    tick: 0.05,
    ltp,
    bid: Math.round((ltp - 0.05) * 100) / 100,
    ask: Math.round((ltp + 0.05) * 100) / 100,
    oi,
    prev_oi: oi - 65 * ((strike / 50) % 2 === 0 ? 1800 : -900) * (side === 'CE' ? 1 : -1),
    volume: 120_000,
    close: Math.round((ltp - (side === 'CE' ? 9.2 : -7.6)) * 100) / 100,
  }
}

function chain(asOf: number) {
  return {
    as_of: asOf,
    exchange: 'NFO',
    underlying: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    expiry: EXPIRY,
    expiry_ts: ist(EXPIRY, '15:40'),
    spot: SPOT,
    pcr: null,
    left_out: 0,
    by_strike: 0,
    strikes: STRIKES.map((strike) => ({ strike, ce: leg(strike, 'CE'), pe: leg(strike, 'PE') })),
  }
}

interface State {
  name: string
  clock: string
  feed: 'ticks' | 'down'
  marketOpen: boolean
  lapsed?: boolean
  chainState?: 'ok' | 'unavailable'
}

/** Answers the app's calls and plays the feed; returns ways to send the open sockets ticks. */
async function serve(page: Page, state: State, path = '/options/NFO/NIFTY') {
  await page.clock.install({ time: new Date(state.clock) })
  const now = Date.parse(state.clock)
  const day = state.clock.slice(0, 10)
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  const window = state.marketOpen ? ['00:00', '23:59'] : ['00:00', '00:01']
  const timings = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX'].map((exchange) => ({
    exchange,
    start_time: ist(day, window[0]),
    end_time: ist(day, window[1]),
  }))
  const seen = { chain: 0, orders: 0, history: [] as string[], subscribed: new Map<string, string>() }
  /** A leg's candles for today, from 09:15 to now, at the interval asked: made up, flat near its price. */
  const history = (body: { symbol?: string; interval?: string }) => {
    const symbol = String(body.symbol ?? '')
    seen.history.push(`${symbol}:${body.interval}`)
    const strike = Number(symbol.slice(12, -2))
    if (!Number.isFinite(strike) || !/(CE|PE)$/.test(symbol)) return json({ status: 'success', data: [] })
    const of = leg(strike, symbol.endsWith('CE') ? 'CE' : 'PE')
    const found = /^(\d+)([mh])$/.exec(String(body.interval ?? '5m'))
    const step = found ? Number(found[1]) * (found[2] === 'h' ? 3600 : 60) : 300
    const data: Array<Record<string, number>> = []
    for (let t = ist(day, '09:15') / 1000, i = 0; t * 1000 <= now; t += step, i += 1) {
      const close = Math.round((of.ltp + Math.sin(i / 3) * 4) * 100) / 100
      data.push({ timestamp: t, open: close - 0.5, high: close + 1, low: close - 1, close, volume: 6500 })
    }
    return json({ status: 'success', data })
  }
  const answers: Record<string, () => ReturnType<typeof json>> = {
    '/auth/session-status': () =>
      json({
        status: 'success',
        authenticated: true,
        logged_in: true,
        broker: 'upstox',
        user: 'owner',
        api_key: 'test-key',
        broker_session_expired: state.lapsed === true,
        active_sessions: 1,
      }),
    '/auth/csrf-token': () => json({ csrf_token: 'test' }),
    '/auth/app-info': () => json({ status: 'success', data: {} }),
    '/auth/analyzer-mode': () => json({ status: 'success', data: { analyze_mode: false } }),
    '/api/broker/capabilities': () =>
      json({ status: 'success', data: { broker_type: 'equity', leverage_config: false } }),
    '/admin/api/timings': () => json({ status: 'success', data: [], market_status: timings }),
    '/admin/api/holidays': () => json({ status: 'success', data: [] }),
    '/api/websocket/config': () =>
      json({ status: 'success', websocket_url: `ws://${new URL(page.url()).host}${WS_PATH}` }),
    '/api/websocket/apikey': () => json({ status: 'success', api_key: 'test-key' }),
    '/sudarshan/risk/state': () => json({ status: 'success', data: { stage: 'off' } }),
    '/sudarshan/options/api/expiries': () =>
      json({
        status: 'success',
        data: {
          state: 'ok',
          words: null,
          listing: {
            underlying: 'NIFTY',
            exchange: 'NFO',
            spot: { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
            future: { symbol: FUTURE, exchange: 'NFO', expiry: '2026-11-24' },
            expiries: ['2026-11-17', '2026-11-24', '2026-12-01', '2026-12-29'].map((date) => ({
              date,
              stops_at: ist(date, '15:40'),
            })),
            now,
          },
        },
      }),
    '/sudarshan/options/api/chain': () => {
      seen.chain += 1
      if (state.lapsed)
        return json({
          status: 'success',
          data: { state: 'signin', words: "Today's Upstox login is needed to read the option chain.", chain: null },
        })
      if (state.chainState === 'unavailable' && seen.chain > 1)
        return json({
          status: 'success',
          data: { state: 'unavailable', words: 'The option chain could not be read.', chain: null },
        })
      return json({ status: 'success', data: { state: 'ok', words: null, chain: chain(now - 1000) } })
    },
    '/sudarshan/watchlists/api/strikes': () =>
      json({ status: 'error', code: 'off', message: 'The new watchlist features are switched off on this server.' }, 503),
    '/api/v1/placeorder': () => {
      seen.orders += 1
      return json({ status: 'error', message: 'no order is sent from this test' }, 400)
    },
  }
  await page.route(
    (url) => /^\/(api|auth|sudarshan|admin|search|socket\.io)\//.test(url.pathname),
    (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.startsWith('/socket.io/')) return route.abort()
      if (url.pathname === '/api/v1/history') return route.fulfill(history(route.request().postDataJSON() ?? {}))
      const answer = answers[url.pathname]
      return route.fulfill(answer ? answer() : json({ status: 'error', message: 'not here' }, 404))
    }
  )

  const open = new Set<WebSocketRoute>()
  const send = (symbol: string, exchange: string, data: Record<string, number>) => {
    for (const socket of open)
      socket.send(JSON.stringify({ type: 'market_data', symbol, exchange, data }))
  }
  /** One tick of everything subscribed: the broker's state on subscribing, or a move. */
  const ticks = (step = 0, slice: number | null = null) => {
    let index = -1
    for (const [key, mode] of seen.subscribed) {
      index += 1
      // A slice is every tenth instrument: the tape spreads one round of ticks over ten sends.
      if (slice !== null && index % 10 !== slice) continue
      const [exchange, symbol] = key.split(':')
      if (symbol === 'NIFTY') send(symbol, exchange, { ltp: SPOT + step * 0.35, prev_close: 25_803.9 })
      else if (symbol === FUTURE) send(symbol, exchange, { ltp: SPOT + 48 + step * 0.35 })
      else if (mode !== 'LTP') {
        const strike = Number(symbol.slice(12, -2))
        const of = leg(strike, symbol.endsWith('CE') ? 'CE' : 'PE')
        const move = ((strike / 50 + step) % 5) * 0.05 * (step % 2 === 0 ? 1 : -1)
        send(symbol, exchange, {
          ltp: Math.round((of.ltp + move) * 100) / 100,
          bid_price: of.bid,
          ask_price: of.ask,
          oi: of.oi + step * 65,
          close: of.close,
          volume: of.volume + step * 65,
        })
      }
    }
  }
  await page.routeWebSocket(
    (url) => url.pathname === WS_PATH,
    (socket) => {
      if (state.feed === 'down') {
        socket.close({ code: 1011, reason: 'feed down' })
        return
      }
      open.add(socket)
      socket.onClose(() => open.delete(socket))
      socket.onMessage((raw) => {
        const message = JSON.parse(String(raw))
        if (message.action === 'authenticate')
          socket.send(JSON.stringify({ type: 'auth', status: 'success' }))
        if (message.action === 'subscribe') {
          for (const each of message.symbols ?? [message])
            seen.subscribed.set(`${each.exchange}:${each.symbol}`, String(message.mode))
          socket.send(JSON.stringify({ type: 'subscribe', status: 'success' }))
          if (state.marketOpen) ticks()
        }
        if (message.action === 'unsubscribe') {
          for (const each of message.symbols ?? [message])
            seen.subscribed.delete(`${each.exchange}:${each.symbol}`)
        }
      })
    }
  )
  await page.goto(path)
  await page.clock.resume()
  await expect(page.getByRole('heading', { level: 1, name: 'NIFTY' })).toBeVisible()
  return { ticks, seen }
}

/** The page's own checks: sideways scroll, small targets, colour-only figures. */
async function pageChecks(page: Page, scope = 'main') {
  return page.evaluate((within) => {
    const small: string[] = []
    let targets = 0
    for (const el of document.querySelectorAll<HTMLElement>(
      `${within} a[href], ${within} button, ${within} [role="switch"], ${within} [role="tab"]`
    )) {
      const box = el.getBoundingClientRect()
      if (box.width === 0 || box.height === 0 || el.closest('.tsqd-parent-container')) continue
      targets += 1
      if (box.width < 47.5 || box.height < 47.5)
        small.push(`${(el.textContent || el.getAttribute('aria-label') || '').trim()} ${box.width}x${box.height}`)
    }
    const figures = [
      ...document.querySelectorAll<HTMLElement>(`${within} [class*="--up)"], ${within} [class*="--down)"]`),
    ].filter((el) => el.textContent?.trim() && el.getBoundingClientRect().width > 0)
    const colourOnly = figures
      .filter((el) => !/[+−]/.test(el.textContent ?? '') && !el.querySelector('[data-direction]'))
      .map((el) => el.textContent?.trim())
    const doc = document.documentElement
    return {
      sideways: doc.scrollWidth > doc.clientWidth,
      targets,
      small,
      figures: figures.length,
      colourOnly,
      realTime: document.querySelectorAll('[data-feed="live"]').length,
    }
  }, scope)
}

async function axeFindings(page: Page, scope = 'main') {
  const scan = await new AxeBuilder({ page }).include(scope).withRules(['color-contrast']).analyze()
  return scan.violations.map((each) => `${each.id}: ${each.nodes.map((node) => node.target).join(' ')}`)
}

const OPEN: State = { name: 'market open with ticks', clock: '2026-11-16T10:42:05+05:30', feed: 'ticks', marketOpen: true }

const STATES: Array<State & { words: Array<string | RegExp>; live: boolean; rows: boolean }> = [
  { ...OPEN, words: ['25,900', 'ATM', /Fut 25,960\.35 \(\+48\.00\)/, '17 Nov · 1d'], live: true, rows: true },
  {
    name: 'market closed',
    clock: '2026-11-16T16:10:00+05:30',
    feed: 'ticks',
    marketOpen: false,
    words: ['Market closed', '25,900'],
    live: false,
    rows: true,
  },
  {
    name: 'feed down',
    clock: '2026-11-16T10:42:05+05:30',
    feed: 'down',
    marketOpen: true,
    words: [/Connecting|Reconnecting/, '25,900'],
    live: false,
    rows: true,
  },
  {
    name: 'Upstox login lapsed',
    clock: '2026-11-16T07:30:00+05:30',
    feed: 'ticks',
    marketOpen: false,
    lapsed: true,
    words: ["Today's Upstox login is needed to read the option chain.", 'Reconnect'],
    live: false,
    rows: false,
  },
]

/**
 * The cost of a tape on one tab: every followed instrument ticks 5 times a second for 10 s, a tenth
 * of them every 20 ms, and each main-thread task is read by the CPU time the thread itself used.
 */
async function costRun(
  page: Page,
  browser: Browser,
  info: TestInfo,
  ticks: (step?: number, slice?: number | null) => void,
  moved: () => Promise<number>
) {
  await page.waitForTimeout(1500) // the load's own work is not what is measured
  await browser.startTracing(page, { categories: ['toplevel', 'disabled-by-default-devtools.timeline'] })
  const began = Date.now()
  for (let step = 1; step <= 50; step += 1) {
    for (let slice = 0; slice < 10; slice += 1) {
      ticks(step, slice)
      await page.waitForTimeout(20)
    }
  }
  const seconds = (Date.now() - began) / 1000
  const ticked = await moved()
  const trace = JSON.parse((await browser.stopTracing()).toString()) as {
    traceEvents: Array<{ name: string; ph: string; pid: number; tid: number; tdur?: number; args?: { name?: string } }>
  }
  // The page's main thread, and each of its tasks by the CPU time the thread itself used (`tdur`,
  // microseconds): what the task costs, whatever else the machine is doing. A mid-range phone is
  // taken as four times slower (design 2.5), so each is scaled by 4 against the budget.
  const mains = new Set(
    trace.traceEvents
      .filter((event) => event.name === 'thread_name' && event.args?.name === 'CrRendererMain')
      .map((event) => `${event.pid}:${event.tid}`)
  )
  const tasks = trace.traceEvents
    .filter((event) => event.name === 'RunTask' && event.ph === 'X' && mains.has(`${event.pid}:${event.tid}`))
    .map((event) => ((event.tdur ?? 0) / 1000) * SLOWER)
    .sort((a, b) => a - b)
  const busy = tasks.filter((ms) => ms >= 1)
  const cost = {
    tasks: tasks.length,
    over50: tasks.filter((ms) => ms > 50).map((ms) => Math.round(ms)),
    largest: Math.round(tasks[tasks.length - 1] ?? 0),
    p95: Math.round((busy[Math.floor(busy.length * 0.95)] ?? 0) * 10) / 10,
    /** The share of each second the main thread would be busy on such a phone. */
    busyShare: Math.round((tasks.reduce((sum, ms) => sum + ms, 0) / (seconds * 1000)) * 100) / 100,
    ticked,
  }
  info.annotations.push({ type: 'cost', description: JSON.stringify(cost) })
  console.log(`cost run (${info.title.slice(0, 40)}): ${JSON.stringify(cost)}`)
  expect(cost.tasks, 'tasks measured').toBeGreaterThan(500)
  expect(cost.over50, 'tasks over 50 ms on a phone four times slower').toEqual([])
  expect(cost.p95, 'a busy task at the 95th percentile, well inside two frames (34 ms)').toBeLessThan(34)
  expect(cost.busyShare, 'the main thread keeps up with the tape').toBeLessThan(0.75)
  return cost
}

const isDevServer = (page: Page) =>
  page.evaluate(() => document.querySelector('script[src*="@vite/client"]') !== null)

test.describe('the options screen at 360 x 800', () => {
  for (const state of STATES) {
    test(state.name, async ({ page }, info) => {
      await serve(page, state)
      for (const words of state.words) await expect(page.getByText(words).first()).toBeVisible()
      if (state.live) await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
      else await expect(page.locator('[data-feed="live"]')).toHaveCount(0)
      await expect(page.locator('[data-strike]')).toHaveCount(state.rows ? 21 : 0)

      const checks = await pageChecks(page)
      expect(checks.sideways, 'no sideways scroll').toBe(false)
      expect(checks.targets, 'targets measured').toBeGreaterThan(state.rows ? 40 : 1)
      expect(checks.small, 'targets under 48 x 48').toEqual([])
      // With the feed down the figures are grey: no up or down ink is claimed for an old move.
      if (state.rows && state.feed !== 'down') expect(checks.figures, 'up and down figures read').toBeGreaterThan(10)
      expect(checks.colourOnly, 'figures told by colour alone').toEqual([])
      expect(await axeFindings(page)).toEqual([])
      await page.screenshot({ path: info.outputPath(`${state.name.replaceAll(' ', '-')}.png`), fullPage: true })
    })
  }

  test('a tapped side opens the strike sheet, and its Buy opens the order ticket without sending anything', async ({ page }, info) => {
    const { seen } = await serve(page, OPEN)
    await page.locator('[data-strike="25900"] [data-leg="CE"]').tap()
    const sheet = page.getByRole('dialog', { name: 'NIFTY 25,900' })
    await expect(sheet.getByRole('button', { name: 'Buy CE' })).toBeVisible()
    await expect(sheet.getByText('Edit row')).toBeHidden()
    await expect(sheet.getByText('Remove row')).toBeHidden()
    await expect(sheet.locator('[data-feed="live"]')).toBeVisible()
    const inSheet = await pageChecks(page, '[role="dialog"]')
    expect(inSheet.small, 'targets under 48 x 48 in the sheet').toEqual([])
    expect(inSheet.colourOnly, 'figures told by colour alone in the sheet').toEqual([])
    expect(inSheet.sideways).toBe(false)
    await page.screenshot({ path: info.outputPath('strike-sheet.png') })

    await page.waitForTimeout(600) // the sheet ignores taps for half a second after it opens
    await sheet.getByRole('button', { name: 'Buy CE' }).tap()
    const ticket = page.getByRole('dialog').filter({ hasText: 'NIFTY17NOV2625900CE' })
    await expect(ticket).toBeVisible()
    await page.screenshot({ path: info.outputPath('ticket.png') })
    await page.goBack()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('[data-strike]')).toHaveCount(21)
    expect(seen.orders, 'no order was sent').toBe(0)
  })

  test('the OI tab: bars with their numbers, the change signed and hatched, the far strikes folded', async ({ page }, info) => {
    await serve(page, OPEN)
    await page.getByRole('tab', { name: 'OI' }).tap()
    await expect(page.locator('[data-oi-facts]')).toContainText(/PCR \d\.\d\d · far strikes as of 10:42:04/)
    await expect(page.locator('[data-oi-facts]')).toContainText(/Max pain \d\d,\d{3} · spot is/)
    await expect(page.locator('[data-oi-strike]')).toHaveCount(41)
    await expect(page.getByRole('button', { name: /20 lower strikes/ })).toBeVisible()
    await expect(page.locator('[data-fell="true"]').first()).toBeVisible()
    const checks = await pageChecks(page)
    expect(checks.sideways, 'no sideways scroll').toBe(false)
    expect(checks.small, 'targets under 48 x 48').toEqual([])
    expect(checks.colourOnly, 'figures told by colour alone').toEqual([])
    expect(await axeFindings(page)).toEqual([])
    await page.screenshot({ path: info.outputPath('oi-tab.png'), fullPage: true })
  })

  test('the IV tab: ATM IV, skew, expected move, the smile and its table', async ({ page }, info) => {
    await serve(page, OPEN)
    await page.getByRole('tab', { name: 'IV' }).tap()
    await expect(page.locator('[data-iv-facts]')).toContainText(/ATM IV\d+\.\d%/)
    await expect(page.locator('[data-smile] [data-line="call"]')).toBeVisible()
    await expect(page.locator('[data-iv-strike]')).toHaveCount(41)
    await expect(page.getByText('IV rank needs stored history, which is not built yet.')).toBeVisible()
    const checks = await pageChecks(page)
    expect(checks.sideways, 'no sideways scroll').toBe(false)
    expect(checks.small, 'targets under 48 x 48').toEqual([])
    expect(await axeFindings(page)).toEqual([])
    await page.screenshot({ path: info.outputPath('iv-tab.png'), fullPage: true })
  })

  test('follows 84 instruments, 83 of them in Quote mode, and never more than 100', async ({ page }) => {
    const { seen } = await serve(page, OPEN)
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    const modes = [...seen.subscribed.values()]
    expect(modes).toHaveLength(84)
    expect(modes.filter((mode) => mode === 'Quote')).toHaveLength(83)
    expect(seen.subscribed.get('NSE_INDEX:NIFTY')).toBe('LTP')
    // A far strike's sheet adds its two legs while it is open, and lets them go after.
    await page.getByRole('button', { name: 'Show more strikes' }).tap()
    await page.getByRole('button', { name: /Show 40 far strikes/ }).tap()
    await page.locator('[data-strike="23900"] [data-leg="PE"]').tap()
    await expect(page.getByRole('dialog', { name: 'NIFTY 23,900' })).toBeVisible()
    await expect.poll(() => seen.subscribed.size).toBe(86)
    await page.goBack()
    await expect.poll(() => seen.subscribed.size).toBe(84)
    expect(seen.chain, 'one snapshot read for all of it').toBe(1)
  })

  test('a failed read keeps the chain, says its time and offers Try again', async ({ page }, info) => {
    await serve(page, { ...OPEN, name: 'read failed', chainState: 'unavailable' })
    await expect(page.locator('[data-strike]')).toHaveCount(21)
    await page.clock.fastForward(6_000)
    await page.getByRole('button', { name: 'Read the option chain again' }).tap()
    await expect(
      page.getByText('The option chain could not be read. Prices keep moving; open interest is as of 10:42:04.')
    ).toBeVisible()
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
    await expect(page.locator('[data-strike]')).toHaveCount(21)
    const checks = await pageChecks(page)
    expect(checks.small).toEqual([])
    expect(checks.sideways).toBe(false)
    await page.screenshot({ path: info.outputPath('read-failed.png') })
  })

  test('back from sleep: reconnecting from the first frame, real-time again with the ticks', async ({ page }, info) => {
    const { ticks, seen } = await serve(page, OPEN)
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    const hide = (hidden: boolean) =>
      page.evaluate((state) => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
        document.dispatchEvent(new Event('visibilitychange'))
      }, hidden ? 'hidden' : 'visible')
    // The phone sleeps for 12 minutes: the shared socket closes 5 s after the page hides.
    await hide(true)
    await page.clock.fastForward('12:00')
    expect(seen.chain, 'nothing is read while the page is hidden').toBe(1)
    await hide(false)
    await expect(page.getByText(/Reconnecting · updated (12|11)m ago/).first()).toBeVisible()
    await expect(page.locator('[data-feed="live"]')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath('waking.png') })
    ticks(1)
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    await expect.poll(() => seen.chain, 'the snapshot is read again after the sleep').toBe(2)
  })

  test('the Straddle tab: a tapped strike, its figures, the computed chart, inside the 84 followed', async ({ page }, info) => {
    const { seen, ticks } = await serve(page, OPEN)
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    await page.getByRole('button', { name: 'Straddle at 25,950' }).tap()
    await expect(page.getByRole('tab', { name: 'Straddle' })).toHaveAttribute('aria-selected', 'true')
    await expect(page).toHaveURL(/tab=straddle&strike=25950/)
    const tab = page.locator('[data-straddle-strike="25950"]')
    await expect(tab).toBeVisible()
    const premium = leg(25_950, 'CE').ltp + leg(25_950, 'PE').ltp
    await expect(tab.locator('[data-straddle-premium]')).toHaveText(/^\d{3}\.\d\d$/)
    const shown = Number(await tab.locator('[data-straddle-premium]').textContent())
    expect(Math.abs(shown - premium), 'call + put, within the tape\'s own move').toBeLessThan(0.5)
    await expect(tab.locator('[data-straddle-change]')).toHaveText(/^[+−]\d+\.\d\d · [+−]\d+\.\d%$/)
    await expect(tab.locator('[data-straddle-breakevens]')).toHaveText(/^25,\d{3}\.\d\d and 26,\d{3}\.\d\d$/)
    await expect(tab.getByText('Computed · not tradable')).toBeVisible()
    // The chart: both legs' candles read once each on 5m, then drawn.
    await expect(tab.locator('[data-straddle-chart] canvas').first()).toBeVisible()
    await expect.poll(() => seen.history).toEqual([`${symbolOf(25_950, 'CE')}:5m`, `${symbolOf(25_950, 'PE')}:5m`])
    await expect(tab.locator('[data-chart-state]')).toHaveCount(0)
    // No instrument was added for it, and none while its legs tick.
    expect(seen.subscribed.size, 'the straddle follows nothing new').toBe(84)
    ticks(1)
    ticks(2)
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    expect(seen.subscribed.size).toBe(84)
    expect(seen.chain, 'one snapshot read for all of it').toBe(1)

    const checks = await pageChecks(page)
    expect(checks.sideways, 'no sideways scroll').toBe(false)
    expect(checks.small, 'targets under 48 x 48').toEqual([])
    expect(checks.colourOnly, 'figures told by colour alone').toEqual([])
    expect(await axeFindings(page)).toEqual([])
    await page.screenshot({ path: info.outputPath('straddle-tab.png'), fullPage: true })

    // Another interval reads both legs again; nothing else changes on the socket.
    await tab.getByRole('button', { name: '15m' }).tap()
    await expect.poll(() => seen.history.slice(2)).toEqual([`${symbolOf(25_950, 'CE')}:15m`, `${symbolOf(25_950, 'PE')}:15m`])
    expect(seen.subscribed.size).toBe(84)

    // "Trade the legs" opens the strike sheet, the put first, and sends nothing.
    await tab.getByRole('button', { name: '25,950 put' }).tap()
    const sheet = page.getByRole('dialog', { name: 'NIFTY 25,950' })
    await expect(sheet.getByRole('button', { name: 'Sell PE' })).toBeVisible()
    await expect(sheet.getByText('Edit row')).toBeHidden()
    await page.screenshot({ path: info.outputPath('straddle-legs.png') })
    await page.goBack()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(seen.orders, 'no order was sent').toBe(0)
  })

  test('a far strike\'s straddle follows its two legs while shown (86), never more than 100', async ({ page }) => {
    const { seen } = await serve(page, OPEN, '/options/NFO/NIFTY?tab=straddle&strike=23900')
    const tab = page.locator('[data-straddle-strike="23900"]')
    await expect(tab).toBeVisible()
    await expect(tab.getByText(/a far strike, followed while shown/)).toBeVisible()
    await expect.poll(() => seen.subscribed.size).toBe(86)
    expect(seen.subscribed.get(`NFO:${symbolOf(23_900, 'CE')}`)).toBe('Quote')
    // Its sheet over it: the same two legs, so still 86 (the other expiries cannot be read here).
    await tab.getByRole('button', { name: '23,900 call' }).tap()
    await expect(page.getByRole('dialog', { name: 'NIFTY 23,900' })).toBeVisible()
    expect(seen.subscribed.size).toBeLessThanOrEqual(100)
    await expect.poll(() => seen.subscribed.size).toBe(86)
    await page.goBack()
    await page.getByRole('tab', { name: 'Chain' }).tap()
    await expect.poll(() => seen.subscribed.size).toBe(84)
  })

  test('the cost run: 84 instruments at 5 ticks a second for 10 s, by the main thread\'s own CPU time', async ({ page, browser }, info) => {
    test.slow()
    const { ticks } = await serve(page, OPEN)
    test.skip(await isDevServer(page), 'the cost run measures a production build: vite build, then vite preview')
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    const cost = await costRun(page, browser, info, ticks, () => page.locator('[data-tick]').count())
    expect(cost.ticked, 'the rows followed the tape').toBeGreaterThan(10)
  })

  test('the cost run on the Straddle tab: the same tape with the computed chart drawing', async ({ page, browser }, info) => {
    test.slow()
    const { ticks, seen } = await serve(page, OPEN, '/options/NFO/NIFTY?tab=straddle')
    test.skip(await isDevServer(page), 'the cost run measures a production build: vite build, then vite preview')
    const tab = page.locator('[data-straddle-strike="25900"]')
    await expect(tab.locator('[data-straddle-chart] canvas').first()).toBeVisible()
    await expect.poll(() => seen.history.length).toBe(2)
    const before = await tab.locator('[data-straddle-premium]').textContent()
    const cost = await costRun(page, browser, info, ticks, async () => {
      ticks(1) // the tape ends where it began: one more step, and the premium must have moved
      await expect(tab.locator('[data-straddle-premium]')).not.toHaveText(before ?? '')
      return 1
    })
    expect(cost.ticked, 'the premium followed the tape').toBe(1)
    expect(seen.subscribed.size, 'still the 84 instruments').toBe(84)
  })
})
