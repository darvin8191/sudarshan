/**
 * The Alerts page needs all three registrations (CLAUDE.md, "Adding a page"), each at its
 * anchor (cross-design R2): the route inside the app shell after /autologin, the known path for
 * Flask after the autologin view, and the menu entry right after Chart. It lives at
 * /market-alerts because upstream's blueprints/alerts.py owns /alerts.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Alerts page registrations', () => {
  it('has a route in App.tsx, inside the app shell, after the auto-login route', () => {
    expect(APP).toContain(
      "const MarketAlerts = lazy(() => import('@/sudarshan/pages/MarketAlerts'))"
    )
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const inside = shell.slice(0, shell.indexOf('</Route>'))
    const route = '<Route path="/market-alerts" element={<MarketAlerts />} />'
    expect(inside).toContain(route)
    expect(inside.indexOf(route)).toBeGreaterThan(inside.indexOf('<Route path="/autologin"'))
  })

  it('is a known path for Flask, after the auto-login view', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    const view =
      '@react_bp.route("/market-alerts", strict_slashes=False)\ndef react_market_alerts():\n    return serve_react_app()'
    expect(flask).toContain(view)
    expect(flask.indexOf(view)).toBeGreaterThan(flask.indexOf('def react_autologin():'))
  })

  it('is in the menu right after Chart, as Alerts with a ringing bell', () => {
    const index = profileMenuItems.findIndex((item) => item.href === '/market-alerts')
    expect(profileMenuItems[index]).toMatchObject({ label: 'Alerts' })
    expect(profileMenuItems[index - 1]?.href).toBe('/chart')
    expect(profileMenuItems[index].icon.displayName).toBe('BellRing')
  })

  it('stays off upstream’s /alerts, whose children are the chart alerts’ JSON API', () => {
    expect(profileMenuItems.some((item) => item.href === '/alerts')).toBe(false)
    expect(APP).not.toContain('<Route path="/alerts"')
  })
})
