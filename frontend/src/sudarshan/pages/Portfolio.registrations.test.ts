/**
 * The Portfolio page needs all three registrations (CLAUDE.md, "Adding a page"): a route that
 * replaces the old redirect to the backtester, the same path known to Flask, and a menu entry
 * right after Watchlist, never between API Key and Action Center.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Portfolio page registrations', () => {
  it('has a marked route in App.tsx, inside the app shell, and no redirect left', () => {
    expect(APP).toContain(
      "const Portfolio = lazy(() => import('@/sudarshan/pages/Portfolio')) // SUDARSHAN M7.18"
    )
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const inside = shell.slice(0, shell.indexOf('</Route>'))
    expect(inside).toContain('<Route path="/portfolio" element={<Portfolio />} />')
    expect(inside).toContain('SUDARSHAN M7.18')
    expect(APP).not.toContain('<Navigate to="/portfolio-backtester" replace />')
    expect(inside).toContain(
      '<Route path="/portfolio-backtester" element={<PortfolioBacktester />} />'
    )
  })

  it('is a known path for Flask, with SUDARSHAN’s comment', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    const view =
      '@react_bp.route("/portfolio")\ndef react_portfolio_legacy():\n    return serve_react_app()'
    expect(flask).toContain(view)
    const before = flask.slice(0, flask.indexOf(view))
    expect(before.slice(before.lastIndexOf('\n\n'))).toContain('SUDARSHAN M7.18')
  })

  it('is in the menu right after Watchlist, as Portfolio with a briefcase', () => {
    const index = profileMenuItems.findIndex((item) => item.href === '/portfolio')
    expect(profileMenuItems[index]).toMatchObject({ label: 'Portfolio' })
    expect(profileMenuItems[index - 1]?.href).toBe('/watchlist')
    expect(profileMenuItems[index].icon.displayName).toBe('Briefcase')
  })

  it('keeps Action Center right after API Key', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(hrefs.indexOf('/action-center')).toBe(hrefs.indexOf('/apikey') + 1)
    expect(hrefs.filter((href) => href === '/portfolio')).toHaveLength(1)
  })
})
