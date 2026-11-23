/**
 * M8.3 GA6.2: the figures beside each price on the watchlist, on a phone upright (360 x 800) and on
 * its side (800 x 360), with normal and Larger text, in a real browser. Every REST call is answered
 * here and the market-data socket plays ticks in the proxy's message shape: nothing reaches Upstox,
 * NSE or Telegram, and no order is sent anywhere. Every name and figure is synthetic.
 *
 *   npx playwright test -c playwright.sd.config.ts e2e/sd-watchlist-figures.spec.ts
 *
 * Each screen asserts: no sideways scroll; every figure drawn is whole (its own width holds its
 * text and it sits inside its row and its block), never cut; how many a row draws (upright, two
 * with normal text and none with Larger text, where a 7rem name and the price leave no room, and
 * the line under the tabs says so; on its side, six); the rows stream in Quote mode only while a
 * figure they draw needs it; and a tick moves the figures it carries. The View sheet's chooser is
 * used on the phone upright: picks saved one by one with their figures, the seventh refused in its
 * words, every row at least 48 x 48 CSS px.
 */
import { expect, type Page, test, type WebSocketRoute } from '@playwright/test'

test.skip(({ browserName }) => browserName !== 'chromium', 'the phone specs run on Chromium only')

const WS_PATH = '/__sd_market'
const IST_MS = 5.5 * 3_600_000
const DAY = '2026-11-23'
const NOW = Date.parse(`${DAY}T11:20:00+05:30`)
const SIX = ['high', 'low', 'open', 'prev', 'volume', 'oi']

/** Epoch ms of an IST wall time on DAY. */
const ist = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number)
  return Date.parse(`${DAY}T00:00:00Z`) - IST_MS + (h * 60 + m) * 60_000
}

/** The rows of the list, each with a made-up quote. */
const ROWS = [
  { symbol: 'INFY', exchange: 'NSE', ltp: 1552.4, prev: 1540.1, high: 1561.9, low: 1536.25, open: 1541, volume: 4_812_330, oi: 0 },
  { symbol: 'TCS', exchange: 'NSE', ltp: 3412.75, prev: 3398.2, high: 3430, low: 3390.5, open: 3401, volume: 1_204_118, oi: 0 },
  { symbol: 'HDFCBANK', exchange: 'NSE', ltp: 1718.3, prev: 1725.65, high: 1729.9, low: 1711.05, open: 1724, volume: 9_650_201, oi: 0 },
  { symbol: 'NIFTY 50', exchange: 'NSE_INDEX', ltp: 25912.35, prev: 25803.9, high: 25950.1, low: 25790.45, open: 25810, volume: 0, oi: 0 },
  { symbol: 'NIFTY24NOV2625900CE', exchange: 'NFO', ltp: 133.5, prev: 120.4, high: 151.2, low: 101.75, open: 118, volume: 81_23_450, oi: 1_23_45_600 },
]

interface Fixture {
  cols: string[]
}

const caught = { outside: [] as string[], unrouted: [] as string[], prefs: [] as unknown[] }
test.beforeEach(() => {
  caught.outside = []
  caught.unrouted = []
  caught.prefs = []
})
test.afterEach(() => {
  expect(caught.outside, 'a request to another host').toEqual([])
})

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

async function serve(page: Page, fixture: Fixture) {
  await page.clock.install({ time: NOW })
  const pref = { sort: 'list', descending: false, basis: 'close', held: false, cols: fixture.cols }
  const timings = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS'].map((exchange) => ({
    exchange,
    start_time: ist('09:15'),
    end_time: ist('15:30'),
  }))
  const contract = (row: (typeof ROWS)[number]) => ({
    exchange: row.exchange,
    symbol: row.symbol,
    key: `${row.exchange}:${row.symbol}`,
    name: row.symbol,
    lot: null,
    tick: 0.05,
    expiry: null,
    isin: null,
  })
  const view = () => ({
    ref: 'list:1',
    kind: 'list',
    name: 'Core',
    version: '3',
    locked: false,
    as_of: null,
    lines: [],
    total: ROWS.length,
    missing: [],
    sections: [],
    rows: ROWS.map((row, index) => ({
      item: index + 1,
      contract: contract(row),
      section_id: null,
      flag: null,
      has_note: false,
      expired: false,
      metric: null,
    })),
    follow: [],
    pref,
  })
  const ok = (data: unknown) => json({ status: 'ok', data })
  const answers: Record<string, (body: unknown) => ReturnType<typeof json>> = {
    '/auth/session-status': () =>
      json({
        status: 'success',
        authenticated: true,
        logged_in: true,
        broker: 'upstox',
        user: 'owner',
        api_key: 'test-key',
        broker_session_expired: false,
        active_sessions: 1,
      }),
    '/auth/csrf-token': () => json({ csrf_token: 'test' }),
    '/auth/app-info': () => json({ status: 'success', data: {} }),
    '/auth/analyzer-mode': () => json({ status: 'success', data: { analyze_mode: true } }),
    '/api/broker/capabilities': () =>
      json({ status: 'success', data: { broker_type: 'equity', leverage_config: false } }),
    '/admin/api/timings': () => json({ status: 'success', data: [], market_status: timings }),
    '/admin/api/holidays': () => json({ status: 'success', data: [] }),
    '/api/websocket/config': () =>
      json({ status: 'success', websocket_url: `ws://${new URL(page.url()).host}${WS_PATH}` }),
    '/api/websocket/apikey': () => json({ status: 'success', api_key: 'test-key' }),
    '/sudarshan/risk/state': () => json({ status: 'success', data: { stage: 'off' } }),
    '/watchlist/api/lists': () =>
      json({
        status: 'success',
        data: [
          {
            id: 1,
            name: 'Core',
            position: 0,
            items: ROWS.map((row, index) => ({
              id: index + 1,
              symbol: row.symbol,
              exchange: row.exchange,
              position: index,
            })),
          },
        ],
      }),
    '/sudarshan/watchlists/api/state': () =>
      ok({
        enabled: true,
        versions: { lists: 'a:1', favourites: 1, flags: 1 },
        flag_names: [],
        favourites: [{ slot: 1, ref: 'list:1', kind: 'list', name: 'Core', count: ROWS.length }],
      }),
    '/sudarshan/watchlists/api/view': () => ok(view()),
    '/sudarshan/watchlists/api/prefs/list%3A1': (body) => {
      caught.prefs.push(body)
      Object.assign(pref, body as object)
      return ok({ version: null, id: null, words: null })
    },
    '/api/v1/multiquotes': () =>
      json({
        status: 'success',
        results: ROWS.map((row) => ({
          symbol: row.symbol,
          exchange: row.exchange,
          data: {
            ltp: row.ltp,
            prev_close: row.prev,
            open: row.open,
            high: row.high,
            low: row.low,
            volume: row.volume,
            oi: row.oi,
            bid: 0,
            ask: 0,
          },
        })),
      }),
  }
  await page.route(
    (url) => !['localhost', '127.0.0.1'].includes(url.hostname),
    (route) => {
      caught.outside.push(route.request().url())
      return route.abort()
    }
  )
  await page.route(
    (url) => /^\/(api|auth|sudarshan|admin|watchlist|search|socket\.io)\//.test(url.pathname),
    (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.startsWith('/socket.io/')) return route.abort()
      const answer = answers[url.pathname]
      if (answer) return route.fulfill(answer(route.request().postDataJSON()))
      caught.unrouted.push(url.pathname)
      return route.fulfill(json({ status: 'error', message: 'Not answered in this test.' }, 404))
    }
  )
  const open = new Set<WebSocketRoute>()
  const subscribed = new Map<string, string>()
  const send = (symbol: string, exchange: string, data: Record<string, number>) => {
    for (const socket of open)
      socket.send(JSON.stringify({ type: 'market_data', symbol, exchange, data }))
  }
  await page.routeWebSocket(
    (url) => url.pathname === WS_PATH,
    (socket) => {
      open.add(socket)
      socket.onClose(() => open.delete(socket))
      socket.onMessage((raw) => {
        const message = JSON.parse(String(raw))
        if (message.action === 'authenticate')
          socket.send(JSON.stringify({ type: 'auth', status: 'success' }))
        if (message.action === 'subscribe') {
          for (const each of message.symbols ?? [message])
            subscribed.set(`${each.exchange}:${each.symbol}`, String(message.mode))
          socket.send(JSON.stringify({ type: 'subscribe', status: 'success' }))
        }
        if (message.action === 'unsubscribe')
          for (const each of message.symbols ?? [message])
            subscribed.delete(`${each.exchange}:${each.symbol}`)
      })
    }
  )
  await page.goto('/watchlist')
  await page.clock.resume()
  await expect(page.getByRole('button', { name: /^INFY/ })).toBeVisible()
  return { send, subscribed }
}

/** Sideways scroll, and every figure as it is drawn: whole, inside its row, and how many show. */
async function figureChecks(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement
    const rows = [...document.querySelectorAll<HTMLElement>('[data-figures]')].map((block) => {
      const box = block.getBoundingClientRect()
      const button = block.closest('button') as HTMLElement
      const outer = button.getBoundingClientRect()
      const cells = [...block.querySelectorAll<HTMLElement>('[data-figure]')]
      const shown = cells.filter((cell) => {
        const at = cell.getBoundingClientRect()
        return at.bottom <= box.bottom + 0.5 && at.top >= box.top - 0.5
      })
      const cut = shown
        .filter((cell) => {
          const at = cell.getBoundingClientRect()
          return (
            cell.scrollWidth > cell.clientWidth ||
            at.left < box.left - 0.5 ||
            at.right > box.right + 0.5 ||
            at.right > outer.right + 0.5
          )
        })
        .map((cell) => cell.dataset.figure)
      return {
        name: (button.textContent ?? '').slice(0, 12),
        shown: shown.map((cell) => cell.dataset.figure),
        cut,
        sizes: {
          row: Math.round(outer.width),
          block: Math.round(box.width),
          price: Math.round((block.nextElementSibling as HTMLElement).getBoundingClientRect().width),
          name: Math.round((block.previousElementSibling as HTMLElement).getBoundingClientRect().width),
        },
      }
    })
    return { sideways: doc.scrollWidth > doc.clientWidth, rows }
  })
}

for (const screen of [
  { label: 'upright', viewport: { width: 360, height: 800 }, shown: { normal: 2, large: 0 } },
  { label: 'on its side', viewport: { width: 800, height: 360 }, shown: { normal: 6, large: 6 } },
]) {
  for (const text of ['normal', 'large'] as const) {
    test.describe(`the figures ${screen.label}, ${text} text`, () => {
      test.use({ viewport: screen.viewport, hasTouch: true, isMobile: true })

      test('are drawn whole, as many as the width holds, and move with the ticks', async ({
        page,
      }, info) => {
        if (text === 'large')
          await page.addInitScript(() => localStorage.setItem('sudarshan-text', 'large'))
        const feed = await serve(page, { cols: SIX })
        const shown = screen.shown[text]
        await expect(page.locator('[data-figures]').first()).toBeAttached()
        const checks = await figureChecks(page)
        info.annotations.push({ type: 'sizes', description: JSON.stringify(checks.rows) })
        expect(checks.sideways, 'sideways scroll').toBe(false)
        expect(checks.rows).toHaveLength(ROWS.length)
        for (const row of checks.rows) {
          expect(row.cut, `${row.name}: a figure cut`).toEqual([])
          expect(row.shown, `${row.name}: figures shown`).toEqual(SIX.slice(0, shown))
        }
        await expect(page.locator('main')).not.toContainText(/Real-time/)
        if (shown === 0) {
          // Nothing drawn: the line says so, and no row streams in Quote mode for figures not shown.
          await expect(
            page.getByText('Figures: 0 of 6 shown here; turn the phone on its side for the rest.')
          ).toBeVisible()
          await expect.poll(() => feed.subscribed.size).toBe(ROWS.length)
          expect(new Set(feed.subscribed.values())).toEqual(new Set(['LTP']))
          return
        }
        // The rows stream in Quote mode while a figure they draw needs it (T1).
        await expect
          .poll(() => [...feed.subscribed.values()].filter((mode) => mode === 'Quote').length)
          .toBe(ROWS.length)
        // A tick moves the figures it carries.
        feed.send('INFY', 'NSE', { ltp: 1563.5, high: 1563.5, low: 1536.25, volume: 4_900_000 })
        await expect(
          page.getByRole('button', { name: /^INFY/ }).locator('[data-figure="high"]')
        ).toContainText('1,563.50')
        if (shown < SIX.length)
          await expect(
            page.getByText('Figures: 2 of 6 shown here; turn the phone on its side for the rest.')
          ).toBeVisible()
        await page.screenshot({
          path: info.outputPath(`watchlist-figures-${screen.viewport.width}-${text}.png`),
        })
      })
    })
  }
}

test.describe('choosing the figures, upright', () => {
  test.use({ viewport: { width: 360, height: 800 }, hasTouch: true, isMobile: true })

  test('saves each pick with its figures, refuses a seventh, and every row is 48 dp', async ({
    page,
  }, info) => {
    await serve(page, { cols: ['high', 'low', 'open', 'prev', 'volume'] })
    await page.getByRole('button', { name: 'List options' }).tap()
    await page.waitForTimeout(700) // the menu's 500 ms guard against a double tap
    await page.getByRole('menuitem', { name: 'View', exact: true }).tap()
    const sheet = page.getByRole('dialog', { name: 'View' })
    await expect(sheet.getByText(/^Pick up to six\./)).toBeVisible()
    await page.waitForTimeout(700) // the sheet's fade and its guard
    const boxes = sheet.getByRole('checkbox')
    await expect(boxes).toHaveCount(8)
    for (const box of await boxes.all()) {
      const size = await box.boundingBox()
      expect(size && size.height >= 47.5 && size.width >= 47.5, 'a row under 48 x 48').toBe(true)
    }
    await sheet.getByRole('checkbox', { name: 'Open interest (OI)' }).click()
    await expect.poll(() => caught.prefs.length).toBe(1)
    expect(caught.prefs[0]).toMatchObject({ cols: ['high', 'low', 'open', 'prev', 'volume', 'oi'] })
    await expect(sheet.getByRole('checkbox', { name: 'Open interest (OI), place 6' })).toBeChecked()
    await sheet.getByRole('checkbox', { name: 'IV (options)' }).click()
    await expect(sheet.getByRole('status')).toHaveText('Six figures at most. Remove one first.')
    expect(caught.prefs).toHaveLength(1)
    await sheet.getByRole('checkbox', { name: 'Day high, place 1' }).click()
    await expect.poll(() => caught.prefs.length).toBe(2)
    expect(caught.prefs[1]).toMatchObject({ cols: ['low', 'open', 'prev', 'volume', 'oi'] })
    await expect(sheet.getByRole('checkbox', { name: 'Day low, place 1' })).toBeChecked()
    expect((await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)), 'sideways scroll').toBe(false)
    await page.screenshot({ path: info.outputPath('watchlist-figures-chooser-360.png') })
  })
})
