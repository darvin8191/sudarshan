import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeScreen } from '@/test/screenWidth'
import { Navbar } from './Navbar'

vi.mock('@/hooks/useProfileMenuItems', () => ({
  useProfileMenuItems: () => [
    {
      href: '/profile',
      label: 'Profile',
      icon: () => null,
    },
  ],
}))

function renderNavbar(pathname: string, props: { fluid?: boolean } = {}) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <Navbar {...props} />
    </MemoryRouter>
  )
}

function currentLinks(container: HTMLElement) {
  return within(container)
    .getAllByRole('link')
    .filter((link) => link.getAttribute('aria-current') === 'page')
}

describe('Navbar', () => {
  // SUDARSHAN (SCR-P item 5): at 360 px the account end grew past the screen in Analyze mode and
  // before the fonts arrived, and the page scrolled sideways. Measured in e2e/sd-dashboard.spec.ts;
  // pinned here, where CI runs, by what makes it hold: the lockup's link is the part that may
  // shrink on a phone, the account end never is, and the bar's lockup wraps its icon away.
  it('lets the lockup, never the account controls, give way on a phone', () => {
    renderNavbar('/dashboard')
    const row = screen.getByTestId('navbar-row')
    const lockup = within(row).getByRole('link', { name: /सुदर्शन\s*SUDARSHAN/ })
    expect(lockup.className.split(/\s+/)).toContain('min-w-0')
    // The link sits in the room the account controls leave, which the lockup's caps measure
    // (v0.13.0 delta review L5): it gives way on a phone and keeps its width from 48rem.
    const classes = (lockup.parentElement as HTMLElement).className.split(/\s+/)
    expect(classes).toContain('sd-lockup-room')
    expect(classes).toContain('min-w-0')
    expect(classes).toContain('flex-1')
    expect(classes).not.toContain('shrink-0')
    expect(classes).toContain('md:shrink-0')
    expect(classes).toContain('md:flex-none')
    expect(row.lastElementChild?.className.split(/\s+/)).toContain('shrink-0')

    const css = readFileSync(join(process.cwd(), 'src/sudarshan/theme.css'), 'utf8')
    const bar = /\.sd-lockup--bar \{([^}]*)\}/.exec(css)?.[1] ?? ''
    expect(bar).toMatch(/flex-flow:\s*row-reverse wrap/)
    expect(bar).toMatch(/overflow:\s*clip/)
    expect(bar).toMatch(/min-width:\s*0/)
    // The caps tighten to the room instead of being cut: tracking and size follow the container.
    const latin = /\.sd-lockup--bar \.sd-lockup__latin \{([^}]*)\}/.exec(css)?.[1] ?? ''
    expect(latin).toMatch(
      /--sd-latin-tracking:\s*clamp\(0\.06em, calc\(\(100cqw - 7em\) \/ 8\), 0\.4em\)/
    )
    expect(latin).toMatch(/font-size:\s*min\(0\.625rem, calc\(100cqw \/ 7\.6\)\)/)
    expect(css).toMatch(/\.sd-lockup-room \{\s*container-type:\s*inline-size/)
  })

  it('marks only the active route as current in desktop navigation', () => {
    renderNavbar('/positions')

    const positions = screen.getByRole('link', { name: 'Positions' })
    const dashboard = screen.getByRole('link', { name: 'Dashboard' })

    expect(positions).toHaveAttribute('aria-current', 'page')
    expect(dashboard).not.toHaveAttribute('aria-current')
    const desktopNav = screen
      .getAllByRole('navigation')
      .find((nav) => nav.classList.contains('md:flex'))

    expect(desktopNav).toBeDefined()
    expect(currentLinks(desktopNav!)).toEqual([positions])
  })

  it('marks only the active sheet navigation link as current', async () => {
    const user = userEvent.setup()
    renderNavbar('/trading')

    await user.click(screen.getByRole('button', { name: 'Toggle menu' }))

    const sheet = await screen.findByRole('dialog', { name: 'Navigation Menu' })
    const trading = within(sheet).getByRole('link', { name: 'Trading' })
    const platforms = within(sheet).getByRole('link', { name: 'Platforms' })

    expect(trading).toHaveAttribute('aria-current', 'page')
    expect(platforms).not.toHaveAttribute('aria-current')
    expect(currentLinks(sheet)).toEqual([trading])
  })

  it('offers no light or dark switch, because the app is light only', () => {
    renderNavbar('/dashboard')

    expect(screen.queryByRole('button', { name: /(dark|light) mode/i })).toBeNull()
    expect(screen.getByRole('button', { name: /^Switch to (Analyze|Live) mode$/ })).toBeVisible()
  })

  it('marks the active quick-access sheet link as current', async () => {
    const user = userEvent.setup()
    renderNavbar('/profile')

    await user.click(screen.getByRole('button', { name: 'Toggle menu' }))

    const sheet = await screen.findByRole('dialog', { name: 'Navigation Menu' })
    const profile = within(sheet).getByRole('link', { name: 'Profile' })
    const trading = within(sheet).getByRole('link', { name: 'Trading' })

    expect(profile).toHaveAttribute('aria-current', 'page')
    expect(trading).not.toHaveAttribute('aria-current')
    expect(currentLinks(sheet)).toEqual([profile])
  })
})

// SUDARSHAN M1.11 (T1): the menu sheet's four headings, and Trading on a phone.
describe('Navbar menu sheet groups', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  async function openSheet(pathname: string) {
    const user = userEvent.setup()
    renderNavbar(pathname)
    await user.click(screen.getByRole('button', { name: 'Toggle menu' }))
    return screen.findByRole('dialog', { name: 'Navigation Menu' })
  }

  it('lists the pages under Trade, Analyse, Automate and System, in that order', async () => {
    const sheet = await openSheet('/dashboard')

    const headings = within(sheet).getAllByRole('heading', { level: 3 })
    expect(headings.map((heading) => heading.textContent)).toEqual([
      'Trade',
      'Analyse',
      'Automate',
      'System',
    ])
    const trade = within(sheet).getByRole('navigation', { name: 'Trade' })
    expect(within(trade).getByRole('link', { name: 'Tradebook' })).toBeVisible()
    const system = within(sheet).getByRole('navigation', { name: 'System' })
    const names = within(system)
      .getAllByRole('link')
      .map((link) => link.textContent)
    expect(names).toEqual(['Profile', 'Logs', 'Docs'])
  })

  it('opens the phone chart from Trading on a phone', async () => {
    fakeScreen('phone')
    const sheet = await openSheet('/chart')

    const trading = within(sheet).getByRole('link', { name: 'Trading' })
    expect(trading).toHaveAttribute('href', '/chart')
    expect(trading).toHaveAttribute('aria-current', 'page')
    const hrefs = within(sheet)
      .getAllByRole('link')
      .map((link) => link.getAttribute('href'))
    expect(hrefs).not.toContain('/trading')
  })

  it('keeps the terminal for Trading on a wide screen', async () => {
    fakeScreen('wide')
    const sheet = await openSheet('/dashboard')

    expect(within(sheet).getByRole('link', { name: 'Trading' })).toHaveAttribute('href', '/trading')
  })

  it('makes every row a 48dp target', async () => {
    const sheet = await openSheet('/dashboard')

    const rows = within(sheet)
      .getAllByRole('link')
      .filter((link) => link.closest('nav'))
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) expect(row).toHaveClass('min-h-12')
  })
})

describe('Navbar More menu', () => {
  const originalMatchMedia = window.matchMedia

  /** Answer the navbar's lg and xl queries as a screen this wide would. */
  function screenWidth(px: number) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches:
        (query === '(min-width: 64rem)' && px >= 1024) ||
        (query === '(min-width: 80rem)' && px >= 1280),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  }

  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  async function moreMenuLabels() {
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'More' }))
    const menu = await screen.findByRole('menu')
    return within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent)
  }

  it('lists only the pages a small laptop bar leaves out', async () => {
    screenWidth(1024)
    renderNavbar('/dashboard')

    expect(await moreMenuLabels()).toEqual(['Platforms', 'Strategies', 'Logs', 'Tools'])
  })

  it('takes Tradebook and Trading as well below lg', async () => {
    screenWidth(800)
    renderNavbar('/dashboard')

    expect(await moreMenuLabels()).toEqual([
      'Tradebook',
      'Trading',
      'Platforms',
      'Strategies',
      'Logs',
      'Tools',
    ])
  })

  it('is underlined only when the current page is inside it', () => {
    screenWidth(1024)
    const { unmount } = renderNavbar('/logs')
    expect(screen.getByRole('button', { name: 'More' })).toHaveClass('text-foreground')
    unmount()

    // Tradebook is in the bar at this width, so More is not the current tab.
    renderNavbar('/tradebook')
    expect(screen.getByRole('button', { name: 'More' })).not.toHaveClass('text-foreground')
  })
})

describe('Navbar width', () => {
  /**
   * The bar itself is always w-full; what changes is the inner wrapper. Pages
   * under Layout share its `container mx-auto`, so the default has to keep
   * that or every ordinary page's nav stops lining up with its content.
   * Full-bleed pages like /trading render this navbar themselves with no such
   * container, and a capped nav floats inset above an edge-to-edge chart.
   */
  function innerWrapper(container: HTMLElement) {
    const nav = container.querySelector('nav')
    if (!nav) throw new Error('no nav element rendered')
    return nav.firstElementChild as HTMLElement
  }

  it('is centred and width-capped by default, matching Layout', () => {
    const { container } = renderNavbar('/dashboard')
    const inner = innerWrapper(container)

    expect(inner.className).toContain('container')
    expect(inner.className).toContain('mx-auto')
    expect(inner.className).not.toContain('w-full')
  })

  it('spans the viewport when fluid, for full-bleed pages', () => {
    const { container } = renderNavbar('/trading', { fluid: true })
    const inner = innerWrapper(container)

    expect(inner.className).toContain('w-full')
    expect(inner.className).not.toContain('container')
    expect(inner.className).not.toContain('mx-auto')
  })
})
