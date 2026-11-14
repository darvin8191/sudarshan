/**
 * /trading on a tablet (M4.4, T10): 1024 x 768 and 768 x 1024 with a touch
 * pointer, on the live look and the analyzer look (this fork is light only, so
 * there is no dark one). A check list, not a build: nothing in /trading's
 * layout is changed for a tablet.
 *
 * At each size and look: the controls pinned at every width (Dashboard, the
 * mode switch and the profile menu; upstream's fourth, the theme toggle, does
 * not exist in a light-only fork and must not come back) are on screen in full
 * and outside the toolbar's scrolling part; the toolbar still carries
 * `data-toolbar-pane`; the layout menu's two and four chart grids can be picked
 * and draw that many panes; a drawing is placed by touch; and the page does not
 * scroll sideways. A screenshot of each is kept for the owner's eye.
 *
 * Nothing reaches a server: every REST answer is a fixture and the market-data
 * socket is answered here. Chromium only, run by hand:
 *   npx playwright test e2e/sd-trading-tablet.spec.ts --project=chromium
 */
import { expect, type Page, test } from '@playwright/test'

test.skip(({ browserName }) => browserName !== 'chromium', 'touch goes through Chromium')

const NOW = Date.UTC(2026, 9, 20, 4, 30, 30)

const json = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

/** Five-minute candles for the five sessions before the page's clock: a seeded walk. */
function history() {
  const rows: Array<Record<string, number>> = []
  let price = 812.4
  let seed = 812
  const random = () => {
    seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648
    return seed / 2_147_483_648
  }
  for (const day of [14, 15, 16, 19, 20])
    for (let i = 0; i < 75; i++) {
      const time = Date.UTC(2026, 9, day, 3, 45) / 1000 + i * 300
      if (time * 1000 > NOW) break
      const open = price
      price = Math.round((price + (random() - 0.5) * 2.4) * 20) / 20
      rows.push({
        timestamp: time,
        open,
        high: Math.max(open, price) + 0.4,
        low: Math.min(open, price) - 0.4,
        close: price,
        volume: 20_000 + Math.round(random() * 60_000),
      })
    }
  return rows
}

async function serve(page: Page, analyze: boolean) {
  await page.clock.install({ time: NOW })
  let polls = 0
  await page.route('**/socket.io/**', async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'POST') return route.fulfill({ body: 'ok' })
    if (!url.searchParams.get('sid'))
      return route.fulfill({
        body: `0${JSON.stringify({ sid: 's1', upgrades: [], pingInterval: 600_000, pingTimeout: 600_000, maxPayload: 1_000_000 })}`,
      })
    if (polls++ === 0) return route.fulfill({ body: '40{"sid":"n1"}' })
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
        broker_session_expired: false,
        active_sessions: 1,
      })
    )
  )
  await page.route('**/auth/csrf-token', (route) => route.fulfill(json({ csrf_token: 'token' })))
  await page.route('**/auth/analyzer-mode', (route) =>
    route.fulfill(
      json({ status: 'success', data: { analyze_mode: analyze, mode: analyze ? 'analyze' : 'live' } })
    )
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
  await page.route('**/sudarshan/session/**', (route) =>
    route.fulfill(
      json({ status: 'success', data: { day: '2026-10-20', stops_at: null, closing: null } })
    )
  )
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/', '')
    if (path === 'history') return route.fulfill(json({ status: 'success', data: history() }))
    if (path === 'intervals')
      return route.fulfill(
        json({
          status: 'success',
          data: { seconds: [], minutes: ['1m', '5m', '15m'], hours: ['1h'], days: ['D'], weeks: ['W'], months: ['M'] },
        })
      )
    if (path === 'symbol')
      return route.fulfill(
        json({ status: 'success', data: { name: 'STATE BANK OF INDIA', lotsize: 1, tick_size: 0.05 } })
      )
    if (path === 'quotes')
      return route.fulfill(json({ status: 'success', data: { ltp: 812.4, prev_close: 810, volume: 4_210_000 } }))
    return route.fulfill(json({ status: 'success', data: [] }))
  })
  await page.routeWebSocket(/\/proxy$/, (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw)) as Record<string, unknown>
      if (message.action === 'authenticate') ws.send(JSON.stringify({ type: 'auth', status: 'success' }))
    })
  })
}

test.use({
  hasTouch: true,
  serviceWorkers: 'block',
  contextOptions: { reducedMotion: 'reduce' },
})

for (const [name, viewport] of [
  ['on its side', { width: 1024, height: 768 }],
  ['upright', { width: 768, height: 1024 }],
] as const)
  for (const look of ['live', 'analyzer'] as const)
    test.describe(`/trading on a tablet ${name}, ${viewport.width} x ${viewport.height}, the ${look} look`, () => {
      test.use({ viewport })

      test('the pinned controls, a drawing by touch, and the grids of two and four', async ({ page }, info) => {
        await serve(page, look === 'analyzer')
        await page.goto('/trading?symbol=SBIN&exchange=NSE')
        await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' })
        await page.clock.runFor(3000)
        // The toolbar names the chart it drives; the browser harness reads this.
        await expect(page.locator('[data-toolbar-pane]').first()).toBeVisible()
        await expect(page.locator('[data-chart-pane]')).toHaveCount(1)

        // The controls pinned at every width, each on screen in full and outside the
        // toolbar's scrolling part. This fork is light only, so no theme toggle is among them.
        const analyze = look === 'analyzer'
        const pinned = [
          page.getByRole('link', { name: 'Dashboard' }),
          page.getByRole('button', { name: analyze ? 'Switch to Live mode' : 'Switch to Analyze mode' }),
          page.getByRole('button', { name: 'Open user menu' }),
        ]
        for (const control of pinned) {
          await expect(control).toBeVisible()
          const box = (await control.boundingBox()) as DOMRect
          expect(box.x).toBeGreaterThanOrEqual(0)
          expect(box.x + box.width).toBeLessThanOrEqual(viewport.width)
          expect(
            await control.evaluate((el) => el.closest('[data-workspace-toolbar]') === null)
          ).toBe(true)
        }
        await expect(page.getByRole('button', { name: /(dark|light) mode/i })).toHaveCount(0)
        const sideways = () =>
          page.evaluate(() => ({
            scroll: document.documentElement.scrollWidth,
            client: document.documentElement.clientWidth,
          }))
        expect((await sideways()).scroll, 'no sideways page scroll').toBeLessThanOrEqual(
          (await sideways()).client
        )
        expect(
          await page.evaluate(() => document.documentElement.classList.contains('analyzer'))
        ).toBe(analyze)
        await page.screenshot({ path: info.outputPath(`trading-${look}.png`) })

        // A drawing placed by touch: the first tool of the rail, then two taps on the chart.
        const pane = (await page.locator('[data-chart-pane]').first().boundingBox()) as DOMRect
        const undo = page.getByRole('button', { name: 'Undo chart change' }).first()
        await expect(undo).toBeDisabled()
        await page.getByRole('button', { name: 'Lines', exact: true }).first().tap()
        // A group with no tool used yet opens its list: the first of it is the trend line.
        const tools = page.getByRole('menu')
        await expect(tools).toBeVisible()
        await tools.getByRole('menuitem').first().tap()
        await expect(tools).toHaveCount(0)
        await expect(page.getByRole('button', { name: 'Lines', exact: true }).first()).toHaveAttribute(
          'aria-pressed',
          'true'
        )
        await page.touchscreen.tap(pane.x + pane.width * 0.35, pane.y + pane.height * 0.55)
        await page.clock.runFor(200)
        await page.touchscreen.tap(pane.x + pane.width * 0.6, pane.y + pane.height * 0.4)
        await page.clock.runFor(500)
        await expect(undo).toBeEnabled()
        await page.screenshot({ path: info.outputPath(`trading-${look}-drawn.png`) })

        // The layout menu's grids of two and four: picked by touch, and that many charts drawn.
        for (const charts of [2, 4]) {
          await page.getByRole('button', { name: /^Chart layout:/ }).tap()
          const menu = page.getByRole('menu')
          await expect(menu).toBeVisible()
          await menu.getByRole('menuitem').filter({ hasText: new RegExp(`^${charts}$`) }).first().tap()
          await page.clock.runFor(2000)
          await expect(page.locator('[data-chart-pane]')).toHaveCount(charts)
          for (const each of await page.locator('[data-chart-pane]').all()) {
            const box = (await each.boundingBox()) as DOMRect
            expect(box.width, 'a pane wide enough to read').toBeGreaterThan(200)
            expect(box.height, 'a pane tall enough to read').toBeGreaterThan(150)
          }
          for (const control of pinned) await expect(control).toBeVisible()
          expect((await sideways()).scroll).toBeLessThanOrEqual((await sideways()).client)
          await page.screenshot({ path: info.outputPath(`trading-${look}-${charts}.png`) })
        }
      })
    })
