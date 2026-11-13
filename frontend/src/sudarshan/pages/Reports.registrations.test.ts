/**
 * The Reports page needs all three registrations (CLAUDE.md, "Adding a page"): a route in the app
 * shell after the Journal, the same path known to Flask, and a menu entry right after Journal.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Reports page registrations', () => {
  it('has a lazy route in App.tsx, inside the app shell, after the Journal', () => {
    expect(APP).toContain("const Reports = lazy(() => import('@/sudarshan/pages/Reports'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const inside = shell.slice(0, shell.indexOf('</Route>'))
    const route = '<Route path="/reports" element={<Reports />} />'
    expect(inside).toContain(route)
    expect(inside.indexOf(route)).toBeGreaterThan(inside.indexOf('<Route path="/journal"'))
  })

  it('is a known path for Flask, with strict_slashes off', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    const view =
      '@react_bp.route("/reports", strict_slashes=False)\ndef react_reports():\n    return serve_react_app()'
    expect(flask).toContain(view)
    expect(flask.indexOf(view)).toBeGreaterThan(flask.indexOf('def react_journal():'))
  })

  it('is in the menu right after Journal, as Reports with a bar-chart file', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    const index = hrefs.indexOf('/reports')
    expect(profileMenuItems[index]).toMatchObject({ label: 'Reports' })
    expect(['FileBarChart2', 'FileChartColumn']).toContain(profileMenuItems[index].icon.displayName)
    expect(hrefs[index - 1]).toBe('/journal')
    expect(hrefs[index + 1]).toBe('/holdings')
    expect(hrefs.filter((href) => href === '/reports')).toHaveLength(1)
  })
})
