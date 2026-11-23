/**
 * M8.3 GA6.2: the figures beside each price on the watchlist, on a phone upright (360 x 800) and on
 * its side (800 x 360), with normal and Larger text, in a real browser. Every REST call is answered
 * here and the market-data socket plays ticks in the proxy's message shape: nothing reaches Upstox,
 * NSE or Telegram, and no order is sent anywhere. Every name and figure is synthetic.
 *
 *   npx playwright test -c playwright.sd.config.ts e2e/sd-watchlist-figures.spec.ts
 *
 * Each screen asserts: no sideways scroll; every figure drawn is whole (its own width holds its
 * text and it sits inside its row and its block), never cut; every name whole beside its figures
 * (v0.17.0 review M1: a BANKNIFTY strike read "BANKNIFTY 5850..." upright), a figure column being
 * left out first; how many a row draws (upright, two where the whole name and the price leave room
 * for them, with either text size, an option's longer name leaving fewer and the line under the
 * tabs then saying "up to"; on its side, six); the rows stream in Quote mode only while a
 * figure they draw needs it; and a tick moves the figures it carries. The View sheet's chooser is
 * used on the phone upright: picks saved one by one with their figures, the seventh refused in its
 * words, every row at least 48 x 48 CSS px.
 *
 * M8.2 TD1: a list holding a contract that expired on 3 Nov 2026 (the server's master no longer
 * lists it) shows "Expired on 3 Nov 2026" in its row, never asks a quote for it or follows it on the
 * socket, never raises the refresh notice, and its sheet asks before it removes the row.
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
  // Review M1: an index option whose name is longer than a 7rem floor, priced in four digits.
  { symbol: 'BANKNIFTY30DEC2658500PE', exchange: 'NFO', ltp: 1048.25, prev: 1002.6, high: 1112.4, low: 962.15, open: 1006, volume: 8_14_220, oi: 19_87_300 },
]
/** M1: the row whose name may leave no room for a figure column upright. */
const LONG = 'BANKNIFTY 58500 PE'

/** TD1: expired on 3 Nov 2026; the server's master no longer lists it. */
const EXPIRED = { symbol: 'NIFTY03NOV2622700PE', exchange: 'NFO', ltp: 0, prev: 0, high: 0, low: 0, open: 0, volume: 0, oi: 0 }

interface Fixture {
  cols: string[]
  /** TD1: the list also holds the expired contract. */
  expired?: boolean
}

const caught = {
  outside: [] as string[],
  unrouted: [] as string[],
  prefs: [] as unknown[],
  asked: [] as string[],
}
test.beforeEach(() => {
  caught.outside = []
  caught.unrouted = []
  caught.prefs = []
  caught.asked = []
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
  const rows = fixture.expired ? [...ROWS, EXPIRED] : ROWS
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
    total: rows.length,
    missing: [],
    sections: [],
    rows: rows.map((row, index) => ({
      item: index + 1,
      contract: contract(row),
      section_id: null,
      flag: null,
      has_note: false,
      expired: row === EXPIRED,
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
            items: rows.map((row, index) => ({
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
        favourites: [{ slot: 1, ref: 'list:1', kind: 'list', name: 'Core', count: rows.length }],
      }),
    '/sudarshan/watchlists/api/view': () => ok(view()),
    // TD1: the expired contract's sheet shows its flag and note.
    '/sudarshan/watchlists/api/symbol': () =>
      ok({
        key: `NFO:${EXPIRED.symbol}`,
        flag: null,
        flag_name: null,
        note: null,
        note_at: null,
        expired: true,
        version: null,
        lists: [{ ref: 'list:1', name: 'Core' }],
      }),
    '/sudarshan/watchlists/api/prefs/list%3A1': (body) => {
      caught.prefs.push(body)
      Object.assign(pref, body as object)
      return ok({ version: null, id: null, words: null })
    },
    // As the server answers: an error entry for a symbol its master lacks, 400 naming them when it
    // lists none of the call (TD1).
    '/api/v1/multiquotes': (body) => {
      const symbols = (body as { symbols: Array<{ symbol: string; exchange: string }> }).symbols
      caught.asked.push(...symbols.map((each) => each.symbol))
      const listed = ROWS.filter((row) => symbols.some((each) => each.symbol === row.symbol))
      if (listed.length === 0)
        return json({ status: 'error', message: 'Not listed.', invalid_symbols: symbols }, 400)
      return json({
        status: 'success',
        results: listed.map((row) => ({
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
      })
    },
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
      // M1: the name, as wide as its words, beside the figures.
      const title = button.querySelector<HTMLElement>('span[title]')
      return {
        name: (title?.textContent ?? button.textContent ?? '').trim(),
        nameCut: !title || title.scrollWidth > title.clientWidth,
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
  { label: 'upright', viewport: { width: 360, height: 800 }, shown: { normal: 2, large: 2 } },
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
          expect(row.nameCut, `${row.name}: the name is cut`).toBe(false)
          if (/ (CE|PE)$/.test(row.name)) {
            // M1: an option's longer name: as many columns as the whole name leaves room for.
            expect(row.shown.length % 2, `${row.name}: a column split`).toBe(0)
            expect(row.shown, `${row.name}: figures shown`).toEqual(SIX.slice(0, row.shown.length))
            expect(row.shown.length).toBeLessThanOrEqual(shown)
          } else expect(row.shown, `${row.name}: figures shown`).toEqual(SIX.slice(0, shown))
        }
        const uneven = checks.rows.some((row) => row.shown.length < shown)
        // Upright, BANKNIFTY's whole name and its price leave no room for a column (measured).
        if (screen.label === 'upright')
          expect(checks.rows.find((row) => row.name === LONG)?.shown).toEqual([])
        await expect(page.locator('main')).not.toContainText(/Real-time/)
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
            page.getByText(
              `Figures: ${uneven ? 'up to ' : ''}2 of 6 shown here; turn the phone on its side for the rest.`
            )
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

test.describe('an expired contract in the list, upright (M8.2 TD1)', () => {
  test.use({ viewport: { width: 360, height: 800 }, hasTouch: true, isMobile: true })

  test('says when it expired, is never read or followed, and asks before it goes', async ({
    page,
  }, info) => {
    const feed = await serve(page, { cols: [], expired: true })
    const row = page.getByRole('button', { name: /^NIFTY 22700 PE/ })
    await expect(row).toContainText('Expired on 3 Nov 2026')
    await expect(row).not.toContainText('No price yet')
    // Past two quiet re-syncs: nothing asks for it, and no notice.
    await page.clock.fastForward('02:10')
    await expect(page.getByText(/Prices could not be refreshed|Prices are not available/)).toHaveCount(0)
    expect(caught.asked).toContain('INFY')
    expect(caught.asked).not.toContain(EXPIRED.symbol)
    expect([...feed.subscribed.keys()]).not.toContain(`NFO:${EXPIRED.symbol}`)
    expect(feed.subscribed.size).toBe(ROWS.length)
    await row.tap()
    await page.waitForTimeout(700) // the sheet's fade and its guard
    const sheet = page.getByRole('dialog', { name: 'NIFTY 22700 PE' })
    await expect(sheet).toContainText('It is no longer quoted or traded. Remove it from Core?')
    await expect(sheet).not.toContainText('Not answered in this test.')
    for (const name of ['Keep it', 'Remove']) {
      const size = await sheet.getByRole('button', { name, exact: true }).boundingBox()
      expect(size && size.height >= 47.5 && size.width >= 47.5, `${name} under 48 x 48`).toBe(true)
    }
    expect([...feed.subscribed.keys()]).not.toContain(`NFO:${EXPIRED.symbol}`)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
      'sideways scroll'
    ).toBe(false)
    await page.screenshot({ path: info.outputPath('watchlist-expired-360.png') })
  })
})
