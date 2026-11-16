/**
 * SUDARSHAN M13 (P8 U1-U6): the TRINETRA page and the dashboard's TRINETRA cards on a phone, 360 x
 * 800 with touch and on its side at 800 x 360. Every answer of the TRINETRA view is the real
 * bridge's, recorded in front of the real sudarshan-signals reading invented documents
 * (src/sudarshan/signals/bridge.answers.json); the app's Socket.IO connection is answered here in
 * Engine.IO's long-polling shape, so the owner room's join is acked and pushes arrive as on the
 * server. Every other request is answered here or refused: nothing reaches Upstox, NSE, Telegram or
 * TRINETRA, and the browser resolves no other host.
 *
 *   npx playwright test e2e/sd-trinetra.spec.ts --project=chromium
 *
 * Each screen asserts: no sideways scroll; every link, button, radio and switch at least 48 x 48
 * CSS px; the freshness words with the age, and "Real-time" only while in the room with fresh
 * readings; every bias coloured up or down carries its arrow (never colour alone); no
 * colour-contrast finding from axe. A screenshot of each is kept as a test artifact.
 */
import { readFileSync } from 'node:fs'
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page, type Route, test } from '@playwright/test'

interface Recorded {
  method: string
  path: string
  status: number
  answer: unknown
}
const RECORDING = JSON.parse(
  readFileSync(new URL('../src/sudarshan/signals/bridge.answers.json', import.meta.url), 'utf8')
) as { now_ms: number; later_ms: number; answers: Record<string, Recorded> }
const ANSWERS = RECORDING.answers
const NOW = RECORDING.now_ms // 09:05 IST, Tue 17 Nov 2026
const LATER = RECORDING.later_ms // 09:20

const json = (body: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

/** The app's Socket.IO connection in Engine.IO v4 long-polling: open, join acked, pushes. */
async function socketRoom(page: Page) {
  const queue: string[] = []
  let held: Route | null = null
  let polls = 0
  const joins: string[] = []
  const flush = () => {
    if (!held || queue.length === 0) return
    const route = held
    held = null
    void route.fulfill({ contentType: 'text/plain; charset=UTF-8', body: queue.splice(0).join('\x1e') })
  }
  await page.route('**/socket.io/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() === 'POST') {
      for (const packet of (request.postData() ?? '').split('\x1e')) {
        const join = /^42(\d+)\["sudarshan_signals_join"/.exec(packet)
        if (join) {
          joins.push(join[1])
          queue.push(`43${join[1]}[{"ok":true}]`)
        }
      }
      await route.fulfill({ contentType: 'text/plain; charset=UTF-8', body: 'ok' })
      flush()
      return
    }
    if (!url.searchParams.get('sid')) {
      await route.fulfill({
        contentType: 'text/plain; charset=UTF-8',
        body: `0${JSON.stringify({ sid: 's1', upgrades: [], pingInterval: 600_000, pingTimeout: 600_000, maxPayload: 1_000_000 })}`,
      })
      return
    }
    if (polls++ === 0) queue.push('40{"sid":"n1"}')
    held = route
    flush()
  })
  return {
    joins,
    push(data: unknown) {
      queue.push(`42${JSON.stringify(['sudarshan_signals_update', data])}`)
      flush()
    },
  }
}

interface Scene {
  clock: number
  state: 'state' | 'state_stale' | 'off'
}

/** Answers the app's calls; the TRINETRA view from the recording. */
async function serve(page: Page, scene: Scene, path: string) {
  await page.clock.install({ time: new Date(scene.clock) })
  // Nothing leaves the browser: any host but this dev server is refused.
  await page.route(
    (url) => url.hostname !== '127.0.0.1' && url.hostname !== 'localhost',
    (route) => route.abort()
  )
  const room = await socketRoom(page)
  const answers: Record<string, () => ReturnType<typeof json>> = {
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
    '/auth/analyzer-mode': () => json({ status: 'success', data: { analyze_mode: false } }),
    '/api/broker/capabilities': () =>
      json({ status: 'success', data: { broker_type: 'equity', leverage_config: false } }),
    '/auth/dashboard-data': () =>
      json({
        status: 'success',
        data: { availablecash: '420000.00', collateral: '0', m2munrealized: '0', m2mrealized: '0', utiliseddebits: '0' },
      }),
    '/api/v1/positionbook': () =>
      json({
        status: 'success',
        data: [
          { symbol: 'NIFTY17NOV2625900CE', exchange: 'NFO', product: 'NRML', quantity: 65, average_price: 120, ltp: 118, pnl: -130, pnlpercent: -1.6 },
          { symbol: 'BANKNIFTY25NOV26FUT', exchange: 'NFO', product: 'NRML', quantity: -30, average_price: 58200, ltp: 58100, pnl: 3000, pnlpercent: 0.2 },
        ],
      }),
    '/api/v1/holdings': () => json({ status: 'success', data: { holdings: [], statistics: {} } }),
    '/api/v1/multiquotes': () => json({ status: 'success', results: [] }),
    '/api/master-contract/status': () => json({ status: 'success', total_symbols: 120345 }),
    '/admin/api/timings': () => json({ status: 'success', data: [], market_status: [] }),
    '/admin/api/holidays': () => json({ status: 'success', data: [] }),
    '/api/websocket/config': () => json({ status: 'success', websocket_url: 'ws://127.0.0.1:1/none' }),
    '/api/websocket/apikey': () => json({ status: 'success', api_key: 'test-key' }),
    '/sudarshan/risk/state': () => json({ status: 'success', data: { stage: 'off' } }),
    '/sudarshan/screener/api/state': () => json({ status: 'success', data: { enabled: false, screens: [] } }),
  }
  const signals = (path: string, method: string): Recorded | undefined => {
    if (path.endsWith('/state')) return ANSWERS[scene.state]
    if (path.endsWith('/timeline')) return ANSWERS.timeline
    if (path.endsWith('/performance')) return ANSWERS.performance_scored
    if (path.endsWith('/settings')) return method === 'GET' ? ANSWERS.settings : ANSWERS.settings_saved
    return undefined
  }
  await page.route(
    (url) =>
      (url.hostname === '127.0.0.1' || url.hostname === 'localhost') &&
      /^\/(api|auth|sudarshan|admin)\//.test(url.pathname),
    (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (path.startsWith('/sudarshan/signals/api/')) {
        const recorded = signals(path, request.method())
        return route.fulfill(
          recorded ? json(recorded.answer, recorded.status) : json({ status: 'error' }, 404)
        )
      }
      const answer = answers[path]
      return route.fulfill(answer ? answer() : json({ status: 'error', message: 'not here' }, 404))
    }
  )
  await page.goto(path)
  await page.clock.resume()
  return room
}

/** The screen's own checks, inside `scope`: sideways scroll, small targets, colour-only marks. */
async function checks(page: Page, scope: string) {
  return page.evaluate((scope) => {
    const small: string[] = []
    let targets = 0
    for (const root of document.querySelectorAll<HTMLElement>(scope)) {
      for (const el of root.querySelectorAll<HTMLElement>(
        'a[href], button, [role="switch"], [role="tab"], label:has(input[type="radio"])'
      )) {
        const box = el.getBoundingClientRect()
        if (box.width === 0 || box.height === 0 || el.closest('.tsqd-parent-container')) continue
        targets += 1
        if (box.width < 47.5 || box.height < 47.5) {
          small.push(`${(el.textContent || el.getAttribute('aria-label') || '').trim()} ${box.width}x${box.height}`)
        }
      }
    }
    const roots = [...document.querySelectorAll<HTMLElement>(scope)]
    const within = (selector: string) => roots.flatMap((root) => [...root.querySelectorAll<HTMLElement>(selector)])
    const coloured = within('[class*="--up)"], [class*="--down)"]').filter((el) => el.textContent?.trim())
    const colourOnly = coloured
      .filter((el) => !/[+−]/.test(el.textContent ?? '') && !el.querySelector('[data-direction]'))
      .map((el) => el.textContent?.trim())
    const doc = document.documentElement
    return {
      sideways: doc.scrollWidth > doc.clientWidth,
      targets,
      small,
      coloured: coloured.length,
      colourOnly,
      realTime: within('[data-updates="real-time"]').length,
      live: /\bLive\b/.test(document.querySelector('main')?.textContent ?? ''),
    }
  }, scope)
}

async function contrast(page: Page, scope: string) {
  const scan = await new AxeBuilder({ page }).include(scope).withRules(['color-contrast']).analyze()
  return scan.violations.map((each) => `${each.id}: ${each.nodes.map((node) => node.target).join(' ')}`)
}

for (const phone of [
  { name: '360 x 800', viewport: { width: 360, height: 800 }, touch: true },
  { name: '800 x 360, on its side', viewport: { width: 800, height: 360 }, touch: true },
]) {
  test.describe(`TRINETRA at ${phone.name}`, () => {
    test.use({ viewport: phone.viewport, deviceScaleFactor: 2, hasTouch: phone.touch, isMobile: phone.touch })

    test('the page at 09:05: fresh, in the room, Real-time; a push moves it', async ({ page }, info) => {
      const room = await serve(page, { clock: NOW, state: 'state' }, '/trinetra')
      const header = page.getByTestId('trinetra-freshness').first()
      await expect(header).toContainText('TRINETRA · updated 09:04:40 · under a minute old')
      await expect(page.locator('[data-updates="real-time"]')).toHaveText('Real-time')
      expect(room.joins.length).toBeGreaterThanOrEqual(1)
      await expect(page.getByRole('article', { name: 'DRISHTI, NIFTY' })).toContainText('FII distribution')
      await expect(page.getByTestId('trinetra-positions')).toContainText('Against the regime')
      await expect(page.getByRole('list', { name: 'Changes, newest first' })).toBeVisible()
      const found = await checks(page, 'main')
      expect(found.sideways, 'no sideways scroll').toBe(false)
      expect(found.targets, 'targets measured').toBeGreaterThan(8)
      expect(found.small, 'targets under 48 x 48').toEqual([])
      expect(found.coloured, 'coloured biases read').toBeGreaterThan(2)
      expect(found.colourOnly, 'bias told by colour alone').toEqual([])
      expect(found.live, '"Live" for a reading').toBe(false)
      expect(await contrast(page, 'main')).toEqual([])
      await page.screenshot({ path: info.outputPath('trinetra-fresh.png'), fullPage: true })

      // A push from the service, through the owner room: the card moves with no read.
      const newer = structuredClone(ANSWERS.state.answer) as { data: { now_ms: number; readings: Array<{ label: string }> } }
      newer.data.now_ms += 1_000
      newer.data.readings[0].label = 'FII distribution, deepening'
      room.push({ cause: 'document', state: newer.data })
      await expect(page.getByRole('article', { name: 'DRISHTI, NIFTY' })).toContainText(
        'FII distribution, deepening'
      )
    })

    test('Why: the sheet fits, its language radios and close are 48 dp', async ({ page }, info) => {
      await serve(page, { clock: NOW, state: 'state' }, '/trinetra')
      await page.getByRole('button', { name: 'Why: DRISHTI, NIFTY' }).click()
      const sheet = page.getByRole('dialog', { name: 'DRISHTI · NIFTY' })
      await expect(sheet.getByRole('radio', { name: 'Hinglish' })).toBeChecked()
      await expect(sheet.getByTestId('why-text')).toContainText('teen sessions se index futures')
      await page.waitForTimeout(700) // the sheet's fade and its 500 ms guard against a double tap
      const box = await sheet.boundingBox()
      expect((box?.x ?? -1) >= 0 && (box?.x ?? 0) + (box?.width ?? 0) <= phone.viewport.width + 0.5).toBe(true)
      const found = await checks(page, '[role="dialog"]')
      expect(found.small, 'targets under 48 x 48 in the sheet').toEqual([])
      expect(found.targets).toBeGreaterThanOrEqual(4)
      await page.screenshot({ path: info.outputPath('trinetra-why.png') })
    })

    test('the Performance tab', async ({ page }, info) => {
      await serve(page, { clock: NOW, state: 'state' }, '/trinetra?view=performance')
      await expect(page.getByRole('region', { name: 'DRISHTI' })).toContainText(
        '32 cases · 56.25% moved the way it said'
      )
      await expect(page.getByRole('region', { name: 'PRABHAT' })).toContainText('too few cases to judge')
      const found = await checks(page, 'main')
      expect(found.sideways).toBe(false)
      expect(found.small).toEqual([])
      expect(await contrast(page, 'main')).toEqual([])
      await page.screenshot({ path: info.outputPath('trinetra-performance.png'), fullPage: true })
    })

    test('the page at 09:20: stale in words, no Real-time, the lapsed gap bias below', async ({ page }, info) => {
      await serve(page, { clock: LATER, state: 'state_stale' }, '/trinetra')
      await expect(page.getByTestId('trinetra-freshness').first()).toContainText(
        'TRINETRA has not published since 09:04. This is its last reading, 15 minutes old.'
      )
      await expect(page.getByRole('region', { name: 'Lapsed readings' })).toContainText('Lapsed 09:15')
      await page.waitForTimeout(500) // the join's ack has come back by now
      const found = await checks(page, 'main')
      expect(found.realTime, 'Real-time while stale').toBe(0)
      expect(found.sideways).toBe(false)
      expect(found.small).toEqual([])
      expect(found.colourOnly).toEqual([])
      expect(await contrast(page, 'main')).toEqual([])
      await page.screenshot({ path: info.outputPath('trinetra-stale.png'), fullPage: true })
    })

    test('the page switched off: its own screen', async ({ page }, info) => {
      await serve(page, { clock: NOW, state: 'off' }, '/trinetra')
      await expect(page.getByText('The TRINETRA view is switched off.')).toBeVisible()
      const found = await checks(page, 'main')
      expect(found.sideways).toBe(false)
      expect(found.small).toEqual([])
      expect(await contrast(page, 'main')).toEqual([])
      await page.screenshot({ path: info.outputPath('trinetra-off.png'), fullPage: true })
    })

    test('the dashboard at 09:05: the gap bias and swing cards', async ({ page }, info) => {
      await serve(page, { clock: NOW, state: 'state' }, '/dashboard')
      const gap = page.getByTestId('sd-trinetra-gap')
      await expect(gap).toContainText('Mild gap down')
      await expect(gap).toContainText('Holds until 09:15')
      await expect(page.getByTestId('sd-trinetra-swing')).toContainText('Swing top forming')
      const scope = '[data-testid="sd-trinetra-gap"], [data-testid="sd-trinetra-swing"]'
      const found = await checks(page, scope)
      expect(found.sideways, 'no sideways scroll').toBe(false)
      expect(found.small, 'targets under 48 x 48').toEqual([])
      expect(found.colourOnly).toEqual([])
      expect(found.realTime, 'no Real-time on a TRINETRA card').toBe(0)
      expect(await contrast(page, '[data-testid="sd-trinetra-gap"]')).toEqual([])
      await gap.scrollIntoViewIfNeeded()
      await page.screenshot({ path: info.outputPath('dashboard-0905.png'), fullPage: true })
    })

    test('the dashboard at 09:20: the gap bias gone after the open, the swing card stale', async ({ page }, info) => {
      await serve(page, { clock: LATER, state: 'state_stale' }, '/dashboard')
      const swing = page.getByTestId('sd-trinetra-swing')
      await expect(swing).toContainText('This is its last reading, 15 minutes old.')
      await expect(page.getByTestId('sd-trinetra-gap')).toHaveCount(0)
      const found = await checks(page, '[data-testid="sd-trinetra-swing"]')
      expect(found.sideways).toBe(false)
      expect(found.small).toEqual([])
      expect(await contrast(page, '[data-testid="sd-trinetra-swing"]')).toEqual([])
      await swing.scrollIntoViewIfNeeded()
      await page.screenshot({ path: info.outputPath('dashboard-0920.png'), fullPage: true })
    })
  })
}
