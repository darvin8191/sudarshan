/**
 * The family view's registrations (CLAUDE.md "Adding a page", with the exception cross-design Y35
 * writes down): its routes are in App.tsx, outside the owner's Layout; the owner's menu has the
 * one Family entry after Risk and none of the viewer's pages; the viewer's own menu is
 * family/navigation.ts. The Flask side (known paths, /sw.js, /offline.html, the shell route) is
 * pinned by the blueprint's tests, not here.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { bottomNavItems, mobileSheetItems, navItems, profileMenuItems } from '@/config/navigation'
import {
  FAMILY_HOME,
  familyBarItems,
  familyMenu,
  familyMoreItems,
  familyTitle,
} from './navigation'
import { meOf } from './testing/fixtures'

const APP = readFileSync(join(process.cwd(), 'src', 'App.tsx'), 'utf8')

const FAMILY_BLOCK = (() => {
  const start = APP.indexOf('<Route path="/family" element={<FamilyLayout />}>')
  return APP.slice(start, APP.indexOf('</Route>', start))
})()

describe('the family routes in App.tsx', () => {
  it.each([
    ['FamilyLayout', "import('@/sudarshan/family/FamilyLayout')"],
    ['FamilyDashboard', "import('@/sudarshan/family/pages/FamilyDashboard')"],
    ['FamilyPositions', "import('@/sudarshan/family/pages/FamilyPositions')"],
    ['FamilyHoldings', "import('@/sudarshan/family/pages/FamilyHoldings')"],
    ['FamilyOrders', "import('@/sudarshan/family/pages/FamilyOrders')"],
    ['FamilyTrades', "import('@/sudarshan/family/pages/FamilyTrades')"],
    ['FamilyWatchlist', "import('@/sudarshan/family/pages/FamilyWatchlist')"],
    ['FamilyPortfolio', "import('@/sudarshan/family/pages/FamilyPortfolio')"],
    ['FamilySettings', "import('@/sudarshan/family/pages/FamilySettings')"],
    ['FamilyLogin', "import('@/sudarshan/family/pages/FamilyLogin')"],
    ['FamilyJoin', "import('@/sudarshan/family/pages/FamilyJoin')"],
    ['FamilyMembers', "import('@/sudarshan/pages/FamilyMembers')"],
  ])('lazy-loads %s', (name, source) => {
    expect(APP).toContain(`const ${name} = lazy(`)
    expect(APP).toContain(source)
  })

  it('registers the shell with every child of the design', () => {
    expect(FAMILY_BLOCK).toContain('<Route index element={<FamilyDashboard />} />')
    for (const [path, page] of [
      ['positions', 'FamilyPositions'],
      ['holdings', 'FamilyHoldings'],
      ['orders', 'FamilyOrders'],
      ['trades', 'FamilyTrades'],
      ['watchlist', 'FamilyWatchlist'],
      ['portfolio', 'FamilyPortfolio'],
      ['settings', 'FamilySettings'],
    ]) {
      expect(FAMILY_BLOCK).toContain(`<Route path="${path}" element={<${page} />} />`)
    }
  })

  it('keeps the family shell outside the owner Layout', () => {
    const owner = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    const ownerBlock = owner.slice(0, owner.indexOf('</Route>'))
    expect(ownerBlock).not.toContain('FamilyLayout')
    expect(ownerBlock).not.toContain('"/family/')
  })

  it('has the public sign-in and join pages beside /login', () => {
    expect(APP).toContain('<Route path="/family/login" element={<FamilyLogin />} />')
    expect(APP).toContain('<Route path="/family/join/:code" element={<FamilyJoin />} />')
    expect(APP.indexOf('/family/login')).toBeLessThan(APP.indexOf('<Route element={<Layout />}>'))
  })

  it('puts the owner Family page inside the owner Layout', () => {
    const owner = APP.slice(APP.indexOf('<Route element={<Layout />}>'))
    expect(owner.slice(0, owner.indexOf('</Route>'))).toContain(
      '<Route path="/family-members" element={<FamilyMembers />} />'
    )
  })
})

describe('the owner menu', () => {
  it('has the Family entry right after Risk', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(profileMenuItems.find((item) => item.href === '/family-members')?.label).toBe('Family')
    expect(hrefs.indexOf('/family-members')).toBe(hrefs.indexOf('/risk') + 1)
  })

  it('leaves Action Center right after API Key', () => {
    const hrefs = profileMenuItems.map((item) => item.href)
    expect(hrefs.indexOf('/action-center')).toBe(hrefs.indexOf('/apikey') + 1)
  })

  it('lists none of the viewer pages (Y35)', () => {
    const everything = [...navItems, ...bottomNavItems, ...mobileSheetItems, ...profileMenuItems]
    for (const item of everything) {
      expect(item.href.startsWith('/family/')).toBe(false)
      expect(item.href === '/family').toBe(false)
    }
  })
})

describe('the viewer menu (family/navigation.ts)', () => {
  it('has Dashboard, Positions, Holdings and Watchlist on the bar', () => {
    expect(familyBarItems.map((item) => [item.label, item.href])).toEqual([
      ['Dashboard', '/family/'],
      ['Positions', '/family/positions'],
      ['Holdings', '/family/holdings'],
      ['Watchlist', '/family/watchlist'],
    ])
  })

  it('has Orders, Trades, Portfolio and Settings behind More', () => {
    expect(familyMoreItems.map((item) => [item.label, item.href])).toEqual([
      ['Orders', '/family/orders'],
      ['Trades', '/family/trades'],
      ['Portfolio', '/family/portfolio'],
      ['Settings', '/family/settings'],
    ])
  })

  it('points only at registered family routes, and none to an owner page', () => {
    for (const item of [...familyBarItems, ...familyMoreItems]) {
      const sub = item.href.replace('/family/', '')
      expect(
        sub === '' ? FAMILY_BLOCK.includes('<Route index') : FAMILY_BLOCK.includes(`path="${sub}"`)
      ).toBe(true)
    }
  })

  // Review M7 (v0.12.0): the Dashboard was at `/family`, outside the installed app's scope
  // `/family/`: Chrome can show its address bar there, and a reload there is not behind the
  // service worker. Fails on the old menu.
  it('keeps every address inside the installed app scope and start address', () => {
    const manifest = JSON.parse(
      readFileSync(join(process.cwd(), 'public/images/sudarshan-family.webmanifest'), 'utf8')
    ) as { scope: string; start_url: string }
    expect(manifest.scope).toBe('/family/')
    for (const item of [...familyBarItems, ...familyMoreItems]) {
      expect(item.href.startsWith(manifest.scope), item.href).toBe(true)
    }
    expect(FAMILY_HOME).toBe(manifest.start_url)
    expect(familyBarItems[0].href).toBe(FAMILY_HOME)
  })

  it('names the Dashboard at its address with and without the slash', () => {
    expect(familyTitle('/family/')).toBe('Dashboard')
    expect(familyTitle('/family')).toBe('Dashboard')
    expect(familyTitle('/family/positions/')).toBe('Positions')
  })

  it('leaves Orders and Trades out for a member whose mask hides orders', () => {
    const labels = (kind: 'viewer' | 'child') =>
      familyMenu(meOf(kind)).more.map((item) => item.label)
    expect(labels('viewer')).toEqual(['Orders', 'Trades', 'Portfolio', 'Settings'])
    expect(labels('child')).toEqual(['Portfolio', 'Settings'])
  })

  it('shows no page that needs a member before the member is known', () => {
    expect(familyMenu(null).more.map((item) => item.label)).toEqual(['Portfolio', 'Settings'])
  })
})
