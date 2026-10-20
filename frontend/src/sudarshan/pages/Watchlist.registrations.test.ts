/**
 * The watchlist page needs all three registrations (CLAUDE.md, "Adding a
 * page"): a route, a known path on the Flask side so a refresh or a bookmark
 * opened while signed out is not a 404 counted against the visitor, and a way
 * in from the navigation.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { bottomNavItems, profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()

describe('the watchlist page registrations', () => {
  it('has a route in App.tsx, inside the app shell', () => {
    const app = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')
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
})
