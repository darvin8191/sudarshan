/**
 * The Screener page needs all three registrations (CLAUDE.md, "Adding a page"), each at its
 * anchor (cross-design R2): the route inside the app shell right after /watchlist, the known
 * path for Flask after the watchlist view, and the menu entry right after Watchlist.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Screener page registrations', () => {
  it('has a route in App.tsx, inside the app shell, right after /watchlist', () => {
    expect(APP).toContain("const Screener = lazy(() => import('@/sudarshan/pages/Screener'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const inside = shell.slice(0, shell.indexOf('</Route>'))
    const route = '<Route path="/screener" element={<Screener />} />'
    expect(inside).toContain(route)
    expect(inside.indexOf(route)).toBeGreaterThan(inside.indexOf('<Route path="/watchlist"'))
  })

  it('is a known path for Flask, after the watchlist view', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    const view =
      '@react_bp.route("/screener", strict_slashes=False)\ndef react_screener():\n    return serve_react_app()'
    expect(flask).toContain(view)
    expect(flask.indexOf(view)).toBeGreaterThan(flask.indexOf('def react_watchlist():'))
  })

  it('is in the menu right after Watchlist and before Portfolio, as Screener (Y36)', () => {
    const index = profileMenuItems.findIndex((item) => item.href === '/screener')
    expect(profileMenuItems[index]).toMatchObject({ label: 'Screener' })
    expect(profileMenuItems[index - 1]?.href).toBe('/watchlist')
    expect(profileMenuItems[index + 1]?.href).toBe('/portfolio')
  })
})
