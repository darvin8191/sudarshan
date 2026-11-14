/**
 * The System page needs all three registrations (CLAUDE.md, "Adding a page"): a route, a known
 * path on the Flask side so a refresh or a bookmark opened while signed out is not a 404 counted
 * against the visitor, and a way in from the navigation (M20.8).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the System page registrations', () => {
  it('has a route in App.tsx, inside the app shell', () => {
    expect(APP).toContain("const System = lazy(() => import('@/sudarshan/pages/System'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    expect(shell.slice(0, shell.indexOf('</Route>'))).toContain(
      '<Route path="/system" element={<System />} />'
    )
  })

  it('is a known path for Flask', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    expect(flask).toContain(
      '@react_bp.route("/system", strict_slashes=False)\ndef react_system():\n    return serve_react_app()'
    )
  })

  it('is in the profile menu, between Admin and About', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(profileMenuItems.find((item) => item.href === '/system')?.label).toBe('System')
    expect(hrefs.indexOf('/system')).toBe(hrefs.indexOf('/admin') + 1)
    expect(hrefs.indexOf('/about')).toBe(hrefs.indexOf('/system') + 1)
  })
})
