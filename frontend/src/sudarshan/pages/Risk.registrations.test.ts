/**
 * The Risk page needs all three registrations (CLAUDE.md, "Adding a page"): a route, a known
 * path on the Flask side so a refresh or a bookmark opened while signed out is not a 404 counted
 * against the visitor, and a way in from the navigation (M20.7).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Risk page registrations', () => {
  it('has a route in App.tsx, inside the app shell', () => {
    expect(APP).toContain("const Risk = lazy(() => import('@/sudarshan/pages/Risk'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    expect(shell.slice(0, shell.indexOf('</Route>'))).toContain(
      '<Route path="/risk" element={<Risk />} />'
    )
  })

  it('is a known path for Flask', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    expect(flask).toContain(
      '@react_bp.route("/risk", strict_slashes=False)\ndef react_risk():\n    return serve_react_app()'
    )
  })

  it('is in the profile menu, after Auto login', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(profileMenuItems.find((item) => item.href === '/risk')?.label).toBe('Risk')
    expect(hrefs.indexOf('/risk')).toBe(hrefs.indexOf('/autologin') + 1)
  })
})
