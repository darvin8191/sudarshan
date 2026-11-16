/**
 * The TRINETRA page needs all three registrations (CLAUDE.md, "Adding a page"): a route, a known
 * path on the Flask side so a refresh or a bookmark opened while signed out is not a 404 counted
 * against the visitor, and a way in from the navigation, shown unfiltered (M13.7).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { menuGroups, profileMenuItems } from '@/config/navigation'
import { PAGE } from '@/sudarshan/signals/types'

const FRONTEND = process.cwd()
const APP = readFileSync(join(FRONTEND, 'src', 'App.tsx'), 'utf8')

describe('the TRINETRA page registrations', () => {
  it("is the contract's page", () => {
    expect(PAGE).toBe('/trinetra')
  })

  it('has a route in App.tsx, inside the app shell', () => {
    expect(APP).toContain("const Trinetra = lazy(() => import('@/sudarshan/pages/Trinetra'))")
    const shell = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    expect(shell.slice(0, shell.indexOf('</Route>'))).toContain(
      '<Route path="/trinetra" element={<Trinetra />} />'
    )
  })

  it('is a known path for Flask', () => {
    const flask = readFileSync(join(FRONTEND, '..', 'blueprints', 'react_app.py'), 'utf8')
    expect(flask).toContain(
      '@react_bp.route("/trinetra", strict_slashes=False)\ndef react_trinetra():\n    return serve_react_app()'
    )
  })

  it('is in the menu under Analyse, after Reports, for every session that has the menu', () => {
    const entry = profileMenuItems.find((item) => item.href === '/trinetra')
    expect(entry).toMatchObject({ label: 'TRINETRA', group: 'Analyse' })
    expect(entry?.icon.displayName).toBe('Eye')
    const analyse = menuGroups(profileMenuItems, { phone: true }).find(
      (section) => section.group === 'Analyse'
    )
    const hrefs = analyse?.items.map((item) => item.href) ?? []
    expect(hrefs[hrefs.indexOf('/trinetra') - 1]).toBe('/reports')
    // The pins other pages hold are kept.
    const at = (href: string) => profileMenuItems.findIndex((item) => item.href === href)
    expect(at('/holdings')).toBe(at('/reports') + 1)
    expect(profileMenuItems.filter((item) => item.href === '/trinetra')).toHaveLength(1)
  })
})
