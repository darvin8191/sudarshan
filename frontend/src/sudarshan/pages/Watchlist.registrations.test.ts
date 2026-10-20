/**
 * The watchlist page needs all three registrations (CLAUDE.md, "Adding a
 * page"): a route, a known path on the Flask side so a refresh or a bookmark
 * opened while signed out is not a 404 counted against the visitor, and a way
 * in from the navigation.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { matchRoutes } from 'react-router'
import { describe, expect, it } from 'vitest'
import { bottomNavItems, profileMenuItems } from '@/config/navigation'
import { chartPath } from '@/sudarshan/watchlist/SymbolSheet'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

/**
 * The app's own chart routes that name an instrument (the phone chart, M4.15):
 * parameters after /chart/, not upstream's /chart/test page.
 */
const CHART_ROUTES = [...APP.matchAll(/<Route path="(\/chart\/:[^"]+)"/g)].map((match) => match[1])

describe('the watchlist page registrations', () => {
  it('has a route in App.tsx, inside the app shell', () => {
    const app = APP
    expect(app).toContain("const Watchlist = lazy(() => import('@/sudarshan/pages/Watchlist'))")
    const shell = app.slice(app.indexOf('<Route element={<Layout />}>'))
    expect(shell.slice(0, shell.indexOf('</Route>'))).toContain(
      '<Route path="/watchlist" element={<Watchlist />} />'
    )
  })

  it('is a known path for Flask', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    expect(flask).toContain(
      '@react_bp.route("/watchlist", strict_slashes=False)\ndef react_watchlist():\n    return serve_react_app()'
    )
  })

  it('leads the phone bottom bar and is in the menu for a wide screen', () => {
    expect(bottomNavItems[0]).toMatchObject({ href: '/watchlist', label: 'Watchlist' })
    expect(profileMenuItems.some((item) => item.href === '/watchlist')).toBe(true)
  })

  // The phone chart arrives with M4.15 (feat/live-charts). Until it is merged
  // the sheet's Chart link has no route to check against; from then on its
  // path must resolve on the app's own route table, symbol and exchange intact.
  it.runIf(CHART_ROUTES.length > 0)("sends the sheet's Chart link to the app's chart route", () => {
    for (const [symbol, exchange] of [
      ['SBIN', 'NSE'],
      ['M&M', 'NSE'],
      ['NIFTY28OCT2625000CE', 'NFO'],
    ]) {
      // Matched as the router matches a location, which decodes it first.
      const matched = matchRoutes(
        CHART_ROUTES.map((path) => ({ path })),
        chartPath(symbol, exchange)
      )?.[0]
      expect(matched?.params).toEqual({ exchange, symbol })
    }
  })
})
