/**
 * The portfolio's Calendar with the shares on the owner's watchlists (gap audit GA8.2, M7.36), on
 * a phone upright (360 x 800) and on its side (800 x 360), in a real browser. Nothing reaches a
 * server: the portfolio's answers are fixtures here and every other request to the app's server
 * is refused and listed, so a test that made one fails. The page's clock is Saturday 14 November
 * 2026 in India, 12:00. Every name and figure is synthetic.
 *
 *   npx playwright test -c playwright.sd.config.ts e2e/sd-calendar.spec.ts
 *
 * Each screen asserts: no sideways scroll; every button and segment at least 48 x 48 CSS px; a
 * watchlist share's row carries the word "Watchlist" with its icon, never the tint alone; the
 * choice of Holdings, Watchlists or All shows the rows it names; the lists say when they were
 * read ("as of") and nothing says "Real-time"; and axe finds no contrast problem in the page.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page, test } from '@playwright/test'

test.skip(({ browserName }) => browserName !== 'chromium', 'the phone specs run on Chromium only')

const NOW = Date.parse('2026-11-14T12:00:00+05:30')
const READ_AT = Date.parse('2026-11-13T23:50:00+05:30')

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

function row(more: Record<string, unknown>) {
  return {
    event_id: 1,
    isin: 'INE000B01018',
    name: 'Beta Bank',
    kind: 'dividend',
    ex_day: 20261203,
    amount_u: 55_000,
    ratio_num: null,
    ratio_den: null,
    held_qty_u: 300_000_000,
    expected_paise: 1_65_000,
    checked_at: Date.parse('2026-11-12T23:52:00+05:30'),
    decided: null,
    holding: true,
    watchlist: false,
    ...more,
  }
}

const ROWS = [
  row({}),
  row({
    event_id: 2,
    isin: 'INE000C01016',
    name: 'Gamma Engineering and Infrastructure Holdings',
    kind: 'bonus',
    ex_day: 20261205,
    amount_u: null,
    ratio_num: 1,
    ratio_den: 1,
    held_qty_u: null,
    expected_paise: null,
    holding: false,
    watchlist: true,
  }),
  row({ event_id: 3, isin: 'INE000D01014', name: 'Delta Power', ex_day: 20261208, watchlist: true }),
  row({
    event_id: 4,
    isin: 'INE000E01012',
    name: 'Epsilon Foods',
    ex_day: 20261020,
    held_qty_u: null,
    expected_paise: null,
    holding: false,
    watchlist: true,
  }),
]

interface Fixture {
  watchlists?: Record<string, unknown>
  rows?: ReadonlyArray<Record<string, unknown>>
}

const caught = { outside: [] as string[], unrouted: [] as string[] }
test.beforeEach(() => {
  caught.outside = []
  caught.unrouted = []
})
test.afterEach(() => {
  expect(caught.outside, 'a request to another host').toEqual([])
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
  const answers: Record<string, () => ReturnType<typeof json>> = {
    '/auth/session-status': () =>
      json({
        status: 'success',
        authenticated: true,
        logged_in: true,
        user: 'owner',
        broker: 'upstox',
        api_key: 'test-key',
        broker_session_expired: false,
        active_sessions: 1,
      }),
    '/auth/csrf-token': () => json({ csrf_token: 'token' }),
    '/auth/app-info': () => json({ status: 'success', version: 'test' }),
    '/auth/analyzer-mode': () =>
      json({ status: 'success', data: { analyze_mode: true, mode: 'analyze' } }),
    '/api/broker/capabilities': () =>
      json({ status: 'success', data: { broker_type: 'stock', broker_name: 'upstox' } }),
    '/sudarshan/risk/state': () => json({ status: 'success', data: { stage: 'off' } }),
    '/sudarshan/portfolio/api/state': () =>
      json({
        status: 'success',
        enabled: true,
        data: {
          service_version: 'test',
          schema_version: 5,
          now: NOW,
          today: 20261114,
          tabs: [],
          members: [],
          accounts: [],
          jobs: [],
          amfi: { on: false, nav_day: null },
          switches: { tax: false, insights: true, goals: false },
        },
      }),
    '/sudarshan/portfolio/api/calendar': () =>
      json({
        status: 'success',
        enabled: true,
        data: {
          from: 20260816,
          to: 20270513,
          listed_future: true,
          calendar: fixture.rows ?? ROWS,
          read_status: { state: 'ok', night: 20261113, words: null },
          watchlists: fixture.watchlists ?? {
            state: 'ok',
            read_at: READ_AT,
            share_count: 3,
            words: null,
          },
        },
      }),
    '/sudarshan/portfolio/api/corporate-actions': () =>
      json({
        status: 'success',
        enabled: true,
        data: {
          events: [],
          to_confirm: [],
          confirmed: [],
          left_out_count: 0,
          reads: [],
          read_status: { state: 'ok', night: 20261113, words: null },
          lot_check: {
            state: 'ok',
            account_id: 1,
            day: 20261113,
            words: null,
            rows: [],
            unrecorded_count: 0,
          },
        },
      }),
  }
  await page.route(
    (url) => /^\/(api|auth|sudarshan|admin|socket\.io|custom-indicators)\//.test(url.pathname),
    (route) => {
      const path = new URL(route.request().url()).pathname
      if (path.startsWith('/socket.io/')) return route.abort()
      const answer = answers[path]
      if (answer) return route.fulfill(answer())
      caught.unrouted.push(path)
      return route.fulfill(json({ status: 'error', message: 'Not answered in this test.' }, 404))
    }
  )
  await page.clock.install({ time: NOW })
  await page.goto('/portfolio?view=calendar')
  await expect(page.getByRole('heading', { name: 'Calendar' })).toBeVisible()
}

/** Sideways scroll and every target under 48 x 48 CSS px in the page. */
async function pageChecks(page: Page) {
  return page.evaluate(() => {
    const small: string[] = []
    for (const el of document.querySelectorAll<HTMLElement>('main button, main a[href], main [role="tab"]')) {
      const box = el.getBoundingClientRect()
      if (box.width === 0 || box.height === 0 || el.closest('.tsqd-parent-container')) continue
      if (box.width < 47.5 || box.height < 47.5) {
        small.push(`${(el.textContent || el.getAttribute('aria-label') || '').trim()} ${box.width}x${box.height}`)
      }
    }
    const doc = document.documentElement
    return { sideways: doc.scrollWidth > doc.clientWidth, small }
  })
}

async function axeFindings(page: Page) {
  const scan = await new AxeBuilder({ page })
    .include('main')
    .withRules(['color-contrast', 'link-in-text-block'])
    .analyze()
  return scan.violations.map((each) => `${each.id}: ${each.nodes.map((node) => node.target).join(' ')}`)
}

/** The names of the rows in one part of the list, in order. */
const names = (page: Page, part: string) =>
  page
    .getByRole('region', { name: part })
    .getByRole('listitem')
    .evaluateAll((items) => items.map((item) => item.firstElementChild?.firstElementChild?.textContent))

/**
 * v0.17.0 phone review L1: each segment's word drawn inside its segment (at 320 px with Larger
 * text "Watchlists" ran 11 px past its own and under All), whichever segment is chosen.
 */
async function segmentChecks(page: Page) {
  return page.getByRole('tablist', { name: 'Show the events of' }).evaluate((list) => {
    const out: string[] = []
    for (const tab of list.querySelectorAll<HTMLElement>('[role="tab"]')) {
      const box = tab.getBoundingClientRect()
      const range = document.createRange()
      range.selectNodeContents(tab)
      const ink = range.getBoundingClientRect()
      if (ink.left < box.left - 0.5 || ink.right > box.right + 0.5)
        out.push(`${tab.textContent}: its word runs past its segment`)
      if (box.width < 47.5 || box.height < 47.5) out.push(`${tab.textContent}: under 48 x 48`)
    }
    const doc = document.documentElement
    if (doc.scrollWidth > doc.clientWidth) out.push('the page scrolls sideways')
    return out
  })
}

/**
 * v0.17.0 phone review L7: every row's name breaks between words, never inside one ("Infrastructur"
 * over "e Holdings" at 320 px with Larger text): each word of it lies on one line.
 */
async function brokenWords(page: Page) {
  return page.locator('main').evaluate((main) => {
    const out: string[] = []
    for (const name of main.querySelectorAll<HTMLElement>('li > div:first-child > span:first-child')) {
      const node = name.firstChild
      if (!node || node.nodeType !== Node.TEXT_NODE) continue
      const words = node.textContent ?? ''
      for (const found of words.matchAll(/\S+/g)) {
        const range = document.createRange()
        range.setStart(node, found.index ?? 0)
        range.setEnd(node, (found.index ?? 0) + found[0].length)
        const lines = new Set([...range.getClientRects()].map((rect) => Math.round(rect.top)))
        if (lines.size > 1) out.push(`"${found[0]}" in "${words}" is broken over ${lines.size} lines`)
      }
    }
    return out
  })
}

for (const viewport of [
  { width: 320, height: 640 },
  { width: 360, height: 800 },
  { width: 800, height: 360 },
]) {
  for (const text of ['usual', 'Larger'] as const) {
    test.describe(`the calendar at ${viewport.width} x ${viewport.height}, ${text} text`, () => {
      test.use({ viewport, hasTouch: true, isMobile: true })

      test('Holdings, Watchlists and All keep their words inside them (L1); names break between words (L7)', async ({
        page,
      }, info) => {
        if (text === 'Larger')
          await page.addInitScript(() => localStorage.setItem('sudarshan-text', 'large'))
        await serve(page)
        const tabs = page.getByRole('tablist', { name: 'Show the events of' })
        for (const name of ['All', 'Watchlists', 'Holdings']) {
          await tabs.getByRole('tab', { name }).click()
          await expect(tabs.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
          expect(await segmentChecks(page), `${name} chosen`).toEqual([])
        }
        await tabs.getByRole('tab', { name: 'All' }).click()
        expect(await brokenWords(page), 'a word broken inside itself').toEqual([])
        expect(
          await page.getByRole('listitem').filter({ hasText: 'Infrastructure' }).count()
        ).toBeGreaterThan(0)
        await page.screenshot({ path: info.outputPath(`calendar-${viewport.width}-${text}.png`) })
      })
    })
  }
}

for (const screen of [
  { label: 'upright', viewport: { width: 360, height: 800 } },
  { label: 'on its side', viewport: { width: 800, height: 360 } },
]) {
  test.describe(`the calendar ${screen.label}`, () => {
    test.use({ viewport: screen.viewport, hasTouch: true, isMobile: true })

    test('marks the watchlists’ shares, and Holdings, Watchlists or All shows the rows each names', async ({
      page,
    }, info) => {
      await serve(page)
      const tabs = page.getByRole('tablist', { name: 'Show the events of' })
      await expect(tabs.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
      expect(await names(page, 'Coming up')).toEqual([
        'Beta Bank',
        'Gamma Engineering and Infrastructure Holdings',
        'Delta Power',
      ])
      expect(await names(page, 'The last 90 days')).toEqual(['Epsilon Foods'])
      const marks = page
        .getByRole('region', { name: /Coming up|The last 90 days/ })
        .getByText('Watchlist', { exact: true })
      await expect(marks).toHaveCount(3)
      for (const mark of await marks.all()) await expect(mark.locator('svg')).toHaveCount(1)
      await expect(page.getByText('Watchlists as of 13 Nov, 23:50 · 3 shares')).toBeVisible()
      await expect(page.locator('main')).not.toContainText(/Real-time/)
      const checks = await pageChecks(page)
      expect(checks.sideways, 'sideways scroll').toBe(false)
      expect(checks.small, 'targets under 48 x 48').toEqual([])
      expect(await axeFindings(page)).toEqual([])
      await page.screenshot({ path: info.outputPath(`calendar-all-${screen.viewport.width}.png`), fullPage: true })

      await tabs.getByRole('tab', { name: 'Holdings' }).click()
      expect(await names(page, 'Coming up')).toEqual(['Beta Bank', 'Delta Power'])
      await expect(page.getByRole('region', { name: 'The last 90 days' })).toHaveCount(0)
      await expect(page.getByText(/Watchlists as of/)).toHaveCount(0)

      await tabs.getByRole('tab', { name: 'Watchlists' }).click()
      expect(await names(page, 'Coming up')).toEqual([
        'Gamma Engineering and Infrastructure Holdings',
        'Delta Power',
      ])
      expect(await names(page, 'The last 90 days')).toEqual(['Epsilon Foods'])
      expect((await pageChecks(page)).sideways, 'sideways scroll').toBe(false)
      await page.screenshot({ path: info.outputPath(`calendar-watchlists-${screen.viewport.width}.png`), fullPage: true })
    })

    test('says why the watchlists’ shares are not shown, and claims nothing is listed for them', async ({
      page,
    }) => {
      const words =
        'Watchlists v2 is switched off on this server, so the calendar shows the events of what you hold only.'
      // Switched off, the service marks no row as a watchlist share's.
      const rows = ROWS.filter((each) => each.holding).map((each) => ({ ...each, watchlist: false }))
      await serve(page, { rows, watchlists: { state: 'off', read_at: null, share_count: 0, words } })
      await expect(page.getByRole('note').filter({ hasText: words })).toBeVisible()
      await expect(page.getByText(/Watchlists as of/)).toHaveCount(0)
      await page.getByRole('tab', { name: 'Holdings' }).click()
      await expect(page.getByText(words)).toHaveCount(0)
      expect(await axeFindings(page)).toEqual([])
    })
  })
}
