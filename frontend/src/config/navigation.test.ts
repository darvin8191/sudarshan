import { CandlestickChart } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import {
  bottomNavItems,
  isActiveRoute,
  menuGroups,
  mobileSheetItems,
  NAV_GROUPS,
  navItems,
  profileMenuItems,
} from './navigation'

// SUDARSHAN M1.11 (T1): the phone menu under four headings.
describe('menu groups', () => {
  const every = [...navItems, ...profileMenuItems]
  const groupOf = (href: string) => every.find((item) => item.href === href)?.group
  const sheet = (phone: boolean) =>
    menuGroups([...profileMenuItems, ...mobileSheetItems], { phone })

  it('names a group for every navbar and profile entry', () => {
    for (const item of every) expect(NAV_GROUPS).toContain(item.group)
  })

  it('puts the trading, analysis and automation pages under their headings, the rest under System', () => {
    const trade = ['/watchlist', '/chart', '/positions', '/orderbook', '/holdings', '/scalping']
    expect([...trade, '/strategy', '/trading', '/tradebook'].map(groupOf)).toEqual(
      Array(9).fill('Trade')
    )
    const analyse = ['/screener', '/market-alerts', '/tools', '/portfolio', '/journal', '/reports']
    expect(analyse.map(groupOf)).toEqual(Array(6).fill('Analyse'))
    expect(['/flow', '/python', '/platforms'].map(groupOf)).toEqual(Array(3).fill('Automate'))
    const system = ['/profile', '/apikey', '/action-center', '/master-contract', '/logs', '/about']
    expect(system.map(groupOf)).toEqual(Array(6).fill('System'))
  })

  it('lists the groups in order and every entry once, in the order given', () => {
    const groups = sheet(false)
    expect(groups.map(({ group }) => group)).toEqual(['Trade', 'Analyse', 'Automate', 'System'])
    const listed = groups.flatMap(({ items }) => items.map((item) => item.href))
    const given = [...new Set([...profileMenuItems, ...mobileSheetItems].map((item) => item.href))]
    expect([...listed].sort()).toEqual([...given].sort()) // nothing hidden, nothing twice
    expect(groups[1].items.map((item) => item.label)).toEqual([
      'Alerts',
      'Screener',
      'Portfolio',
      'Journal',
      'Reports',
      'Tools',
    ])
    // API Key and Action Center stay side by side, as the profile menu pins them.
    const system = groups[3].items.map((item) => item.href)
    expect(system.indexOf('/action-center')).toBe(system.indexOf('/apikey') + 1)
  })

  it('opens the phone chart for Trading on a phone, folded into the Chart entry', () => {
    const phone = sheet(true).find(({ group }) => group === 'Trade')?.items ?? []
    expect(phone.map((item) => item.href)).not.toContain('/trading')
    expect(phone.filter((item) => item.href === '/chart').map((item) => item.label)).toEqual([
      'Chart',
    ])
    const wide = sheet(false).find(({ group }) => group === 'Trade')?.items ?? []
    expect(wide.find((item) => item.label === 'Trading')?.href).toBe('/trading')
  })

  it('sends Trading itself to the phone chart when the menu has no Chart, and an entry with no group to System', () => {
    const trading = {
      href: '/trading',
      label: 'Trading',
      icon: CandlestickChart,
      group: 'Trade' as const,
    }
    const loose = { href: '/somewhere', label: 'Somewhere', icon: CandlestickChart }
    const groups = menuGroups([trading, loose], { phone: true })
    expect(groups).toEqual([
      { group: 'Trade', items: [{ ...trading, href: '/chart' }] },
      { group: 'System', items: [loose] },
    ])
    expect(trading.href).toBe('/trading') // the list itself is left as it was
  })
})

describe('Navigation Config', () => {
  describe('navItems', () => {
    it('contains the expected main navigation items', () => {
      expect(navItems).toHaveLength(9)

      const labels = navItems.map((item) => item.label)
      expect(labels).toContain('Dashboard')
      expect(labels).toContain('Tools')
      expect(labels).toContain('Orderbook')
      expect(labels).toContain('Positions')
      expect(labels).toContain('Platforms')
      expect(labels).toContain('Trading')
      expect(labels).toContain('Strategies')

      // Strategies sits directly after Platforms. Asserting the position, not
      // just its presence, is the point: it is where the user expects to find
      // it, and a later insertion that pushed it elsewhere should fail here.
      expect(labels.indexOf('Strategies')).toBe(labels.indexOf('Platforms') + 1)
    })

    it('all items have required properties', () => {
      navItems.forEach((item) => {
        expect(item).toHaveProperty('href')
        expect(item).toHaveProperty('label')
        expect(item).toHaveProperty('icon')
        expect(item.href).toMatch(/^\//)
        expect(item.label.length).toBeGreaterThan(0)
      })
    })
  })

  describe('bottomNavItems', () => {
    it('contains exactly 4 items for mobile bottom nav', () => {
      expect(bottomNavItems).toHaveLength(4)
    })

    // SUDARSHAN M8.2: led by the watchlist, as the owner's broker apps are.
    it('has the correct order: Watchlist, Dashboard, Orders, Positions', () => {
      const items = bottomNavItems.map((item) => [item.label, item.href])
      expect(items).toEqual([
        ['Watchlist', '/watchlist'],
        ['Dashboard', '/dashboard'],
        ['Orders', '/orderbook'],
        ['Positions', '/positions'],
      ])
    })

    it('leaves the Tradebook to the menu, which lists every page the bar does not', () => {
      expect(bottomNavItems.map((item) => item.href)).not.toContain('/tradebook')
      expect(mobileSheetItems.map((item) => item.href)).toContain('/tradebook')
    })
  })

  describe('mobileSheetItems', () => {
    it('excludes items already in bottomNavItems', () => {
      const bottomPaths = bottomNavItems.map((item) => item.href)
      const sheetPaths = mobileSheetItems.map((item) => item.href)

      sheetPaths.forEach((path) => {
        expect(bottomPaths).not.toContain(path)
      })
    })

    it('contains remaining nav items', () => {
      const sheetLabels = mobileSheetItems.map((item) => item.label)
      expect(sheetLabels).toContain('Trading')
      expect(sheetLabels).toContain('Platforms')
      expect(sheetLabels).toContain('Logs')
    })
  })

  describe('profileMenuItems', () => {
    it('contains profile-related menu items', () => {
      const labels = profileMenuItems.map((item) => item.label)
      expect(labels).toContain('Profile')
      expect(labels).toContain('API Key')
      expect(labels).toContain('Holdings')
      // Action Center moved from the main navbar into the profile dropdown,
      // positioned right after API Key.
      expect(labels).toContain('Action Center')
      expect(labels.indexOf('Action Center')).toBe(labels.indexOf('API Key') + 1)
    })

    it('lists the SUDARSHAN Auto login page (M2.20)', () => {
      const entry = profileMenuItems.find((item) => item.href === '/autologin')
      expect(entry?.label).toBe('Auto login')
    })

    it('lists the SUDARSHAN phone chart (M4.15)', () => {
      const entry = profileMenuItems.find((item) => item.href === '/chart')
      expect(entry?.label).toBe('Chart')
    })

    it('lists the watchlist for a wide screen, where the bottom bar is hidden (M8.2)', () => {
      const entry = profileMenuItems.find((item) => item.href === '/watchlist')
      expect(entry?.label).toBe('Watchlist')
    })
  })

  describe('isActiveRoute', () => {
    it('returns true for exact matches', () => {
      expect(isActiveRoute('/dashboard', '/dashboard')).toBe(true)
      expect(isActiveRoute('/orderbook', '/orderbook')).toBe(true)
      expect(isActiveRoute('/positions', '/positions')).toBe(true)
    })

    it('returns false for non-matching routes', () => {
      expect(isActiveRoute('/dashboard', '/orderbook')).toBe(false)
      expect(isActiveRoute('/positions', '/holdings')).toBe(false)
    })

    it('does not prefix match', () => {
      // No nav item owns nested pages, so neither a child path nor a longer
      // name that merely starts with the href may light the tab up.
      expect(isActiveRoute('/dashboard/sub', '/dashboard')).toBe(false)
      expect(isActiveRoute('/orderbookextra', '/orderbook')).toBe(false)
    })
  })
})
