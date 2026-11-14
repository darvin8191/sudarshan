/**
 * SUDARSHAN D9 (screens design 8.3): the dashboard on a phone, 360 x 800 with touch (320 and 412
 * wide with `SD_WIDTH`), in each state
 * of its table (market open with ticks, market closed, the feed down, the Upstox login lapsed)
 * and back from sleep. Every REST call is answered here and the market-data socket is a feed
 * synthesised in the proxy's message shape: nothing reaches Upstox, NSE or Telegram, and no
 * recorded market data is used. A screenshot of each state is kept as a test artifact.
 *
 *   npx playwright test e2e/sd-dashboard.spec.ts --project=chromium
 *
 * Each state asserts: no sideways scroll; every link, button and switch at least 48 x 48 CSS px
 * (a link stretched over its tile counts as the tile); the freshness words for the state, and no
 * "Real-time" without ticks; every up or down figure carries a sign or an arrow, not colour alone;
 * and no colour-contrast or colour-only link finding from axe inside the page.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page, test } from '@playwright/test'

/** The phone's width: 360 unless said (`SD_WIDTH=320`, `SD_WIDTH=412`). */
const WIDTH = Number(process.env.SD_WIDTH) || 360
test.use({ viewport: { width: WIDTH, height: 800 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true })

const WS_PATH = '/__sd_market'
const IST_MS = 5.5 * 3_600_000

/** Epoch ms of an IST wall time: ist('2026-11-17', '09:15'). */
function ist(day: string, hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return Date.parse(`${day}T00:00:00Z`) - IST_MS + (h * 60 + m) * 60_000
}

/** The server's day facts for an ordinary trading day (instrument_session.day_facts). */
function dayFacts(day: string, next: string, expiries: string[] = []) {
  const hours = (open: string, close: string, cash: boolean) => ({
    pre_open: cash ? ist(day, '09:00') : null,
    open: ist(day, open),
    close: ist(day, close),
    auction_until: null,
  })
  return {
    day,
    trading_day: true,
    note: null,
    occasion: null,
    exchanges: {
      NSE: hours('09:15', '15:30', true),
      BSE: hours('09:15', '15:30', true),
      NFO: hours('09:15', '15:40', false),
      BFO: hours('09:15', '15:40', false),
      MCX: hours('09:00', '23:55', false),
    },
    expiries: expiries.map((underlying) => ({
      underlying,
      exchange: 'NFO',
      day,
      stops_at: ist(day, '15:40'),
    })),
    stock_expiries: [],
    next_open: ist(next, '09:15'),
  }
}

const INDICES: Record<string, { ltp: number; prev_close: number }> = {
  NIFTY: { ltp: 25912.35, prev_close: 25803.9 },
  BANKNIFTY: { ltp: 58104.2, prev_close: 58162.4 },
  INDIAVIX: { ltp: 12.41, prev_close: 12.17 },
}
const POSITION = 'NIFTY17NOV2625900CE'

interface State {
  name: string
  clock: string
  feed: 'ticks' | 'down'
  lapsed?: boolean
  next: string
  expiries?: string[]
  /** The platform is in Analyze mode (the sandbox): the app bar's chip is the wider one. */
  analyze?: boolean
}

/** Answers the app's calls and plays the feed; returns a way to send the open sockets a tick. */
async function serve(page: Page, state: State) {
  const day = state.clock.slice(0, 10)
  await page.clock.install({ time: new Date(state.clock) })
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  const timings = (['NSE', 'BSE', 'NFO', 'BFO', 'MCX'] as const).map((exchange) => {
    const facts = dayFacts(day, state.next).exchanges[exchange]
    return { exchange, start_time: facts.open, end_time: facts.close }
  })
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
    '/auth/analyzer-toggle': () =>
      json({ status: 'success', data: { analyze_mode: true, message: 'Analyze mode on' } }),
    '/auth/analyzer-mode': () => json({ status: 'success', data: { analyze_mode: state.analyze === true } }),
    '/api/broker/capabilities': () =>
      json({ status: 'success', data: { broker_type: 'equity', leverage_config: false } }),
    '/auth/dashboard-data': () =>
      state.lapsed
        ? json({ status: 'error', code: 'BROKER_SESSION_EXPIRED' }, 401)
        : json({
            status: 'success',
            data: {
              availablecash: '420000.00',
              collateral: '0',
              m2munrealized: '724.75',
              m2mrealized: '4200.00',
              utiliseddebits: '257000.00',
            },
          }),
    '/api/v1/positionbook': () =>
      json({
        status: 'success',
        data: [
          {
            symbol: POSITION,
            exchange: 'NFO',
            product: 'NRML',
            quantity: 65,
            average_price: 131.2,
            ltp: 142.35,
            pnl: 724.75,
            pnlpercent: 8.5,
          },
        ],
      }),
    '/api/v1/holdings': () => json({ status: 'success', data: { holdings: [], statistics: {} } }),
    '/api/v1/multiquotes': () =>
      json({
        status: 'success',
        results: Object.entries(INDICES).map(([symbol, quote]) => ({
          symbol,
          exchange: 'NSE_INDEX',
          data: quote,
        })),
      }),
    '/api/master-contract/status': () => json({ status: 'success', total_symbols: 120345 }),
    '/admin/api/timings': () => json({ status: 'success', data: [], market_status: timings }),
    '/admin/api/holidays': () => json({ status: 'success', data: [] }),
    '/api/websocket/config': () =>
      json({ status: 'success', websocket_url: `ws://${new URL(page.url()).host}${WS_PATH}` }),
    '/api/websocket/apikey': () => json({ status: 'success', api_key: 'test-key' }),
    '/sudarshan/session/today': () =>
      json({ status: 'success', data: dayFacts(day, state.next, state.expiries) }),
    '/sudarshan/autologin/status': () =>
      json({ status: 'success', data: { enabled: true, running: false, next_run: `${day}T08:35:00+05:30` } }),
    '/sudarshan/risk/state': () => json({ status: 'success', data: { stage: 'off' } }),
    '/sudarshan/screener/api/state': () =>
      json({ status: 'success', data: { enabled: false, screens: [] } }),
  }
  await page.route(
    (url) => /^\/(api|auth|sudarshan|admin|socket\.io)\//.test(url.pathname),
    (route) => {
      const path = new URL(route.request().url()).pathname
      if (path.startsWith('/socket.io/')) return route.abort()
      const answer = answers[path]
      return route.fulfill(answer ? answer() : json({ status: 'error', message: 'not here' }, 404))
    }
  )

  const open = new Set<import('@playwright/test').WebSocketRoute>()
  const tick = (symbol: string, exchange: string, ltp: number, prevClose?: number) => {
    for (const socket of open) {
      socket.send(
        JSON.stringify({
          type: 'market_data',
          symbol,
          exchange,
          data: { ltp, ...(prevClose ? { prev_close: prevClose } : {}) },
        })
      )
    }
  }
  const ticks = () => {
    for (const [symbol, quote] of Object.entries(INDICES)) {
      tick(symbol, 'NSE_INDEX', quote.ltp, quote.prev_close)
    }
    tick(POSITION, 'NFO', 145)
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
        if (message.action === 'authenticate') {
          socket.send(JSON.stringify({ type: 'auth', status: 'success' }))
        }
        if (message.action === 'subscribe') {
          socket.send(JSON.stringify({ type: 'subscribe', status: 'success' }))
          ticks()
        }
      })
    }
  )
  await page.goto('/dashboard')
  await page.clock.resume()
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
  return { ticks }
}

/** The page's own checks: sideways scroll, small targets, colour-only figures. */
async function pageChecks(page: Page) {
  return page.evaluate(() => {
    const small: string[] = []
    let targets = 0
    for (const el of document.querySelectorAll<HTMLElement>(
      'main a[href], main button, main [role="switch"], nav a[href], nav button'
    )) {
      const box = el.getBoundingClientRect()
      if (box.width === 0 || box.height === 0 || el.closest('.tsqd-parent-container')) continue
      targets += 1
      let width = box.width
      let height = box.height
      const after = getComputedStyle(el, '::after')
      if (after.position === 'absolute' && after.content !== 'none') {
        let holder = el.parentElement
        while (holder && getComputedStyle(holder).position === 'static') holder = holder.parentElement
        const tile = holder?.getBoundingClientRect()
        if (tile) {
          width = Math.max(width, tile.width)
          height = Math.max(height, tile.height)
        }
      }
      if (width < 47.5 || height < 47.5) {
        small.push(`${(el.textContent || el.getAttribute('aria-label') || '').trim()} ${width}x${height}`)
      }
    }
    const figures = [
      ...document.querySelectorAll<HTMLElement>('main [class*="--up)"], main [class*="--down)"]'),
    ].filter((el) => el.textContent?.trim())
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
  })
}

async function axeFindings(page: Page) {
  const scan = await new AxeBuilder({ page })
    .include('main')
    .withRules(['color-contrast', 'link-in-text-block'])
    .analyze()
  return scan.violations.map((each) => `${each.id}: ${each.nodes.map((node) => node.target).join(' ')}`)
}

const STATES: Array<State & { words: Array<string | RegExp>; live: boolean }> = [
  {
    name: 'market open with ticks',
    clock: '2026-11-17T11:00:00+05:30',
    feed: 'ticks',
    next: '2026-11-18',
    expiries: ['NIFTY'],
    words: [/Open · closes 15:30/, 'Expiry today: NIFTY · stops 15:40', 'NIFTY 25,900 CE'],
    live: true,
  },
  {
    name: 'market closed',
    clock: '2026-11-16T16:10:00+05:30',
    feed: 'ticks',
    next: '2026-11-17',
    words: [/Closed · opens Tue 09:15/, 'Market closed'],
    live: false,
  },
  {
    name: 'feed down',
    clock: '2026-11-17T11:00:00+05:30',
    feed: 'down',
    next: '2026-11-18',
    words: [/Open · closes 15:30/, /Connecting|Reconnecting/],
    live: false,
  },
  {
    name: 'Upstox login lapsed',
    clock: '2026-11-17T07:30:00+05:30',
    feed: 'ticks',
    lapsed: true,
    next: '2026-11-18',
    words: ["Prices and orders need today's Upstox login", 'Upstox login needed', 'Auto login runs at 08:35'],
    live: false,
  },
]

test.describe(`the dashboard at ${WIDTH} x 800`, () => {
  // The app bar (SCR-P item 5). Its account end grows: "Analyze" is wider than "Live", and until
  // the faces arrive the lockup's tracked caps are a fifth wider in the fallback face. Before the
  // fix each widened the page by 5 to 12 px at 360 (and the faces are refused on a dev server
  // whose node_modules is a link, which is how the four states above first failed). The lockup's
  // icon now steps aside instead; both scripts and every control stay inside the screen.
  for (const bar of [
    { name: 'in Analyze mode', analyze: true, fonts: true },
    { name: 'before the fonts arrive', analyze: false, fonts: false },
    { name: 'in Analyze mode before the fonts arrive', analyze: true, fonts: false },
  ]) {
    test(`the app bar never widens the page: ${bar.name}`, async ({ page }, info) => {
      if (!bar.fonts) await page.route('**/*.woff2', (route) => route.abort())
      await serve(page, { ...STATES[0], analyze: bar.analyze })
      await expect(page.getByTestId('status-row')).toHaveAttribute('data-ready', 'true')
      await expect(page.getByTestId('navbar-row').getByText(bar.analyze ? 'Analyze' : 'Live').last()).toBeVisible()
      const measured = await page.evaluate(() => {
        const doc = document.documentElement
        const row = document.querySelector('[data-testid="navbar-row"]') as HTMLElement
        const right = (el: Element | null) => el?.getBoundingClientRect().right ?? Number.NaN
        const link = row.querySelector('a[href="/dashboard"]')
        return {
          sideways: doc.scrollWidth - doc.clientWidth,
          menu: right(row.querySelector('[aria-label="Open user menu"]')) - doc.clientWidth,
          // How far each script reaches past the link that clips it: none for the Devanagari.
          wordmark: right(row.querySelector('.sd-lockup__wordmark')) - right(link),
        }
      })
      await page.screenshot({ path: info.outputPath(`bar-${bar.name.replaceAll(' ', '-')}.png`) })
      expect(measured.sideways, 'no sideways scroll').toBeLessThanOrEqual(0)
      expect(measured.menu, 'the profile menu ends inside the screen').toBeLessThanOrEqual(0)
      expect(measured.wordmark, 'the Devanagari name is whole').toBeLessThanOrEqual(0.5)
      await expect(page.getByTestId('navbar-row').getByText('SUDARSHAN')).toBeVisible()
    })
  }

  // The app shell's toast (SCR-P item 6): its close button was a 20 px circle on the toast's corner
  // and a success sentence read 3.9:1 on its fill. Switching mode raises a success toast.
  test('the shell toast: a 48 dp close button and a success sentence that keeps its contrast', async ({ page }, info) => {
    await serve(page, STATES[0])
    await expect(page.getByTestId('status-row')).toHaveAttribute('data-ready', 'true')
    await page.getByRole('button', { name: 'Switch to Analyze mode' }).tap()
    const toast = page.locator('[data-sonner-toast][data-type="success"]').first()
    await expect(toast).toContainText('Switched to Analyze mode')
    await expect(toast).toHaveAttribute('data-mounted', 'true')
    await page.waitForTimeout(600) // the toast's own slide, so axe reads it at full opacity
    const close = toast.getByRole('button', { name: 'Close toast' })
    const box = await close.boundingBox()
    const around = await toast.boundingBox()
    expect(box?.width, 'close button width').toBeGreaterThanOrEqual(47.5)
    expect(box?.height, 'close button height').toBeGreaterThanOrEqual(47.5)
    // Inside the toast, and clear of the sentence.
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual((around?.x ?? 0) + (around?.width ?? 0) + 0.5)
    const words = await toast.locator('[data-title]').boundingBox()
    expect((words?.x ?? 0) + (words?.width ?? 0)).toBeLessThanOrEqual((box?.x ?? 0) + 0.5)
    const scan = await new AxeBuilder({ page }).include('[data-sonner-toaster]').withRules(['color-contrast']).analyze()
    expect(scan.violations.map((each) => `${each.id}: ${each.nodes.map((node) => node.failureSummary).join(' ')}`)).toEqual([])
    expect(scan.passes.some((each) => each.id === 'color-contrast'), 'the toast was read').toBe(true)
    await page.screenshot({ path: info.outputPath('toast.png') })
    await close.tap()
    await expect(toast).toHaveCount(0)
  })

  for (const state of STATES) {
    test(state.name, async ({ page }, info) => {
      await serve(page, state)
      await expect(page.getByTestId('status-row')).toHaveAttribute('data-ready', 'true')
      for (const words of state.words) {
        await expect(page.getByText(words).first()).toBeVisible()
      }
      if (state.live) await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
      else await expect(page.locator('[data-feed="live"]')).toHaveCount(0)

      const checks = await pageChecks(page)
      expect(checks.sideways, 'no sideways scroll').toBe(false)
      expect(checks.targets, 'targets measured').toBeGreaterThan(10)
      expect(checks.small, 'targets under 48 x 48').toEqual([])
      if (!state.lapsed) expect(checks.figures, 'up and down figures read').toBeGreaterThan(2)
      expect(checks.colourOnly, 'figures told by colour alone').toEqual([])
      expect(await axeFindings(page)).toEqual([])
      await page.screenshot({ path: info.outputPath(`${state.name.replaceAll(' ', '-')}.png`), fullPage: true })
    })
  }

  test('back from sleep: reconnecting from the first frame, real-time again with the ticks', async ({ page }, info) => {
    const { ticks } = await serve(page, STATES[0])
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()

    // The phone sleeps for 12 minutes: the shared socket closes 5 s after the page hides.
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await page.clock.fastForward('12:00')
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    // Within a frame of the wake nothing says Real-time over the old figures.
    await expect(page.getByText(/Reconnecting · updated (12|11)m ago/).first()).toBeVisible()
    await page.screenshot({ path: info.outputPath('waking.png') })
    // The socket comes back, signs in and subscribes; the next ticks make it real-time again.
    ticks()
    await expect(page.locator('[data-feed="live"]').first()).toBeVisible()
    await expect(page.getByText(/Open · closes 15:30/)).toBeVisible()
  })
})
