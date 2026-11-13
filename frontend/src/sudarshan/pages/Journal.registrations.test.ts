/**
 * The Journal page needs all three registrations (CLAUDE.md, "Adding a page"): a route inside the
 * app shell after the other SUDARSHAN pages, the same path known to Flask (a refresh or a bookmark
 * opened while signed out is not a 404 counted against the visitor's IP), and a menu entry before
 * Holdings, in the order Watchlist, Portfolio, Screener, Journal (Reports follows it, JD6).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Journal page registrations', () => {
  it('has a lazy route in App.tsx, inside the app shell, after the other SUDARSHAN pages', () => {
    expect(APP).toContain("const Journal = lazy(() => import('@/sudarshan/pages/Journal'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const inside = shell.slice(0, shell.indexOf('</Route>'))
    const route = '<Route path="/journal" element={<Journal />} />'
    expect(inside).toContain(route)
    for (const earlier of ['/watchlist', '/screener', '/market-alerts', '/risk']) {
      expect(inside.indexOf(route)).toBeGreaterThan(inside.indexOf(`<Route path="${earlier}"`))
    }
  })

  it('is a known path for Flask, with strict_slashes off', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    const view =
      '@react_bp.route("/journal", strict_slashes=False)\ndef react_journal():\n    return serve_react_app()'
    expect(flask).toContain(view)
    expect(flask.indexOf(view)).toBeGreaterThan(flask.indexOf('def react_screener():'))
  })

  it('is in the menu after Screener and before Holdings, as Journal with a notebook', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    const index = hrefs.indexOf('/journal')
    expect(profileMenuItems[index]).toMatchObject({ label: 'Journal' })
    expect(profileMenuItems[index].icon.displayName).toBe('NotebookPen')
    expect(hrefs.indexOf('/watchlist')).toBeLessThan(hrefs.indexOf('/portfolio'))
    expect(hrefs.indexOf('/portfolio')).toBeLessThan(hrefs.indexOf('/screener'))
    expect(hrefs[index - 1]).toBe('/screener')
    expect(hrefs[index + 1]).toBe('/holdings')
    expect(hrefs.filter((href) => href === '/journal')).toHaveLength(1)
  })
})
