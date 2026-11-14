/**
 * The Automation page needs all three registrations (CLAUDE.md, "Adding a page"): a route, a
 * known path on the Flask side and a way in from the menu (M12.1). Its path is the contract's.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileMenuItems } from '@/config/navigation'
import { AUTOMATION_PAGE } from '@/sudarshan/automation/types'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the Automation page registrations', () => {
  it('has a lazy route in App.tsx, inside the app shell', () => {
    expect(AUTOMATION_PAGE).toBe('/automation')
    expect(APP).toContain("const Automation = lazy(() => import('@/sudarshan/pages/Automation'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    expect(shell.slice(0, shell.indexOf('</Route>'))).toContain(
      '<Route path="/automation" element={<Automation />} />'
    )
  })

  it('is a known path for Flask', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    expect(flask).toContain(
      '@react_bp.route("/automation", strict_slashes=False)\ndef react_automation():\n    return serve_react_app()'
    )
  })

  it('is in the profile menu, after Family', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(profileMenuItems.find((item) => item.href === '/automation')?.label).toBe('Automation')
    expect(hrefs.indexOf('/automation')).toBe(hrefs.indexOf('/family-members') + 1)
  })
})
