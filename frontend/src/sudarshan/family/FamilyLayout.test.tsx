import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FamilyBadge, WIDE_ONLY } from './FamilyBadge'
import { FamilyLayout } from './FamilyLayout'
import { resetFamilyManagerForTests } from './familyManager'
import FamilyLogin from './pages/FamilyLogin'
import { useFamilyMe } from './session'
import { FakeSocket, fail, meOf, ok, routeFetch } from './testing/fixtures'

function Probe({ label }: { label: string }) {
  const me = useFamilyMe()
  return <p>{`${label} for ${me.member.handle}`}</p>
}

const PATHS = [
  '/family',
  '/family/positions',
  '/family/holdings',
  '/family/orders',
  '/family/trades',
  '/family/watchlist',
  '/family/portfolio',
  '/family/settings',
]

function mount(path: string, login: ReactNode = <p>The sign-in page</p>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/family/login" element={login} />
          <Route path="/family" element={<FamilyLayout />}>
            <Route index element={<Probe label="Dashboard" />} />
            {PATHS.slice(1).map((p) => (
              <Route key={p} path={p.replace('/family/', '')} element={<Probe label={p} />} />
            ))}
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  window.localStorage.clear()
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => {
  cleanup()
  resetFamilyManagerForTests()
  vi.unstubAllGlobals()
})

describe('the badge', () => {
  it.each(PATHS)('is on %s, with the lock and the words', async (path) => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount(path)
    const badge = await screen.findByTestId('family-badge')
    expect(badge).toHaveAccessibleName('Family, view only')
    expect(badge.textContent).toContain('FAMILY · VIEW ONLY')
    expect(badge.textContent).not.toContain('LIMITED')
    // The lock is an icon beside the words: the badge is never colour alone.
    expect(badge.querySelector('svg')).not.toBeNull()
  })

  it('says LIMITED for a child, so hidden figures read as by design', async () => {
    routeFetch({ '/family/api/me': ok(meOf('child')) })
    mount('/family/positions')
    const badge = await screen.findByTestId('family-badge')
    expect(badge).toHaveAccessibleName('Family, view only, limited')
    expect(badge.textContent).toContain('FAMILY · VIEW ONLY · LIMITED')
  })

  it('is drawn in the family colour with white text', () => {
    render(<FamilyBadge />)
    const badge = screen.getByTestId('family-badge')
    expect(badge.className).toContain('var(--family)')
    expect(badge.className).toContain('text-white')
  })

  it('sits in the sticky app bar, outside what scrolls', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    const badge = await screen.findByTestId('family-badge')
    expect(badge.closest('header')?.className).toContain('sticky')
  })
})

// Review H2 (v0.12.0): at 360 px the title was cut to one letter for a viewer, and a child's
// badge pushed the initial off the screen. jsdom lays nothing out, so these pin the structure the
// fit rests on (each fails on the old bar); the widths themselves are measured in a browser.
describe('the app bar on a narrow phone', () => {
  it.each(['viewer', 'child'] as const)(
    'gives the %s title a line of its own, whole, outside the row the badge is in',
    async (kind) => {
      routeFetch({ '/family/api/me': ok(meOf(kind)) })
      mount('/family/portfolio')
      const title = await screen.findByRole('heading', { level: 1, name: 'Portfolio' })
      const bar = screen.getByTestId('family-bar')
      expect(bar.contains(title)).toBe(false)
      expect(title.closest('header')).toBe(bar.closest('header'))
      expect(title.className).not.toContain('truncate')
      // the row holds the badge and the initial, and nothing in it is the title
      expect(within(bar).getByTestId('family-badge')).toBeInTheDocument()
      expect(within(bar).getByRole('img', { name: 'Signed in as member_one' })).toBeInTheDocument()
    }
  )

  it('lets the badge give way before the initial does', async () => {
    routeFetch({ '/family/api/me': ok(meOf('child')) })
    mount('/family')
    const badge = await screen.findByTestId('family-badge')
    const classes = badge.className.split(/\s+/)
    expect(classes).toContain('min-w-0')
    expect(classes).not.toContain('shrink-0')
    const initial = screen.getByRole('img', { name: 'Signed in as member_one' })
    expect(initial.className.split(/\s+/)).toContain('shrink-0')
  })

  it('shortens a child badge below 440 px, keeping the lock and the full spoken name', () => {
    render(<FamilyBadge limited />)
    const badge = screen.getByTestId('family-badge')
    const family = badge.querySelector('[data-part="family"]') as HTMLElement
    expect(family.textContent).toContain('FAMILY')
    expect(family.className).toBe(WIDE_ONLY)
    expect(WIDE_ONLY.split(' ')).toContain('hidden')
    // what is left when that part is hidden
    expect(badge.textContent?.replace(family.textContent ?? '', '')).toBe('VIEW ONLY · LIMITED')
    expect(badge).toHaveAccessibleName('Family, view only, limited')
    expect(badge.querySelector('svg')).not.toBeNull()
  })

  it('never shortens a viewer badge', () => {
    render(<FamilyBadge />)
    const badge = screen.getByTestId('family-badge')
    expect(badge.querySelector('[data-part="family"]')).toBeNull()
    expect(badge.textContent).toBe('FAMILY · VIEW ONLY')
  })
})

describe('the session', () => {
  it('shows nothing of the screen until the viewer API has said who is signed in', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    expect(screen.queryByText(/Dashboard for/)).toBeNull()
    expect(await screen.findByText('Dashboard for member_one')).toBeInTheDocument()
  })

  it('sends a signed-out phone to the family login', async () => {
    routeFetch({ '/family/api/me': fail('signed_out', 'Your sign-in has expired. Sign in again.') })
    mount('/family/positions')
    expect(await screen.findByText('The sign-in page')).toBeInTheDocument()
  })

  it('checks again when the page comes back into view', async () => {
    const router = routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    await screen.findByText('Dashboard for member_one')
    expect(router.urls().filter((url) => url === '/family/api/me')).toHaveLength(1)
    routeFetch({ '/family/api/me': fail('signed_out', 'Your sign-in has expired. Sign in again.') })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(await screen.findByText('The sign-in page')).toBeInTheDocument()
  })

  it('says in a sentence that the service cannot be reached, with a way to try again', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    mount('/family')
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('cannot be reached')
    expect(alert.textContent).not.toMatch(/fetch|TypeError|\d{3}/)
    expect(within(alert).getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('says the family view is switched off, in the service words', async () => {
    routeFetch({
      '/family/api/me': fail('off', 'The family view is switched off on the server.'),
    })
    mount('/family')
    expect(
      await screen.findByText('The family view is switched off on the server.')
    ).toBeInTheDocument()
  })
})

// Review M1 (v0.12.0): a session the server ended landed on the bare sign-in form, and the
// service's sentence was thrown away. The first and third fail on the old shell.
describe('a session the server ended', () => {
  const PAUSED = 'The account owner has paused this sign-in. Ask them to resume it.'
  const EXPIRED = 'Your sign-in has expired. Sign in again.'
  const WAS_IN = 'sd-family-was-in'

  it('shows the service sentence above the sign-in form, on a phone that was signed in', async () => {
    window.localStorage.setItem(WAS_IN, '1')
    routeFetch({ '/family/api/me': fail('signed_out', PAUSED) }, { '/family/api/me': 401 })
    mount('/family/positions', <FamilyLogin />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    const notice = await screen.findByTestId('family-notice')
    expect(notice.textContent).toBe(PAUSED)
    expect(notice.querySelector('svg')).not.toBeNull() // never colour alone
    expect(document.body.textContent).not.toMatch(/signed_out|401/)
    // said once: the next visit to the form is the bare one
    expect(window.localStorage.getItem(WAS_IN)).toBeNull()
  })

  it('shows the bare form on a phone that never had a session', async () => {
    routeFetch(
      { '/family/api/me': fail('signed_out', 'You were signed out on this device.') },
      { '/family/api/me': 401 }
    )
    mount('/family', <FamilyLogin />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByTestId('family-notice')).toBeNull()
    expect(screen.queryByText('You were signed out on this device.')).toBeNull()
  })

  it('says why when the session ends while the member is using the app', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family', <FamilyLogin />)
    await screen.findByText('Dashboard for member_one')
    routeFetch({ '/family/api/me': fail('signed_out', EXPIRED) }, { '/family/api/me': 401 })
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect((await screen.findByTestId('family-notice')).textContent).toBe(EXPIRED)
    expect(screen.queryByText(/Dashboard for/)).toBeNull()
  })

  it('gives way to the sentence about a sign-in that did not match', async () => {
    window.localStorage.setItem(WAS_IN, '1')
    const wrong = 'That sign-in name, password or code did not match.'
    routeFetch(
      {
        '/family/api/me': fail('signed_out', PAUSED),
        '/family/api/login': fail('signin_failed', wrong),
      },
      { '/family/api/me': 401, '/family/api/login': 401 }
    )
    mount('/family', <FamilyLogin />)
    await screen.findByTestId('family-notice')
    await userEvent.type(screen.getByLabelText('Sign-in name'), 'member_one')
    await userEvent.type(screen.getByLabelText('Password'), 'a sentence that is wrong')
    await userEvent.type(screen.getByLabelText('6-digit code'), '000000')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toBe(wrong)
    expect(screen.queryByTestId('family-notice')).toBeNull()
  })
})

describe('the viewer bottom bar', () => {
  it('is Dashboard, Positions, Holdings, Watchlist and More, and no owner entries', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    const nav = await screen.findByRole('navigation', { name: 'Family' })
    const names = within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent)
    expect(names).toEqual(['Dashboard', 'Positions', 'Holdings', 'Watchlist'])
    expect(within(nav).getByRole('button', { name: /More/ })).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: /Exit|Buy|Sell|Order ticket|Risk|Analyze/i })
    ).toBeNull()
  })

  it('marks the current page, with more than colour', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family/holdings')
    const nav = await screen.findByRole('navigation', { name: 'Family' })
    expect(within(nav).getByRole('link', { name: 'Holdings' })).toHaveAttribute(
      'aria-current',
      'page'
    )
  })

  it('puts Orders, Trades, Portfolio and Settings behind More, for a member who may see orders', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    const nav = await screen.findByRole('navigation', { name: 'Family' })
    await userEvent.click(within(nav).getByRole('button', { name: /More/ }))
    const sheet = await screen.findByRole('dialog')
    expect(
      within(sheet)
        .getAllByRole('link')
        .map((link) => link.textContent)
    ).toEqual(['Orders', 'Trades', 'Portfolio', 'Settings'])
  })

  it('leaves Orders and Trades out, not greyed, when the mask hides orders', async () => {
    routeFetch({ '/family/api/me': ok(meOf('child')) })
    mount('/family')
    const nav = await screen.findByRole('navigation', { name: 'Family' })
    await userEvent.click(within(nav).getByRole('button', { name: /More/ }))
    const sheet = await screen.findByRole('dialog')
    const names = within(sheet)
      .getAllByRole('link')
      .map((link) => link.textContent)
    expect(names).toEqual(['Portfolio', 'Settings'])
    expect(screen.queryByText('Orders')).toBeNull()
    expect(screen.queryByText('Trades')).toBeNull()
  })
})

// Review M7 (v0.12.0): the Dashboard link and the sign-in redirect led to `/family`, outside the
// installed app's scope `/family/`. Both fail on the old shell.
describe('the Dashboard address', () => {
  function Where() {
    const location = useLocation()
    return <p data-testid="where">{location.pathname}</p>
  }

  it('is /family/ on the bar, and stays marked as the current page', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family/')
    const nav = await screen.findByRole('navigation', { name: 'Family' })
    const link = within(nav).getByRole('link', { name: 'Dashboard' })
    expect(link).toHaveAttribute('href', '/family/')
    expect(link).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Positions' })).not.toHaveAttribute('aria-current')
  })

  it('moves a phone that opened /family to /family/', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/family']}>
          <Where />
          <Routes>
            <Route path="/family" element={<FamilyLayout />}>
              <Route index element={<Probe label="Dashboard" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
    await screen.findByText('Dashboard for member_one')
    expect(screen.getByTestId('where').textContent).toBe('/family/')
  })
})

describe('what the shell does not mount', () => {
  it('has no Analyze banner, kill switch, order ticket or owner navigation', async () => {
    routeFetch({ '/family/api/me': ok(meOf('viewer')) })
    mount('/family')
    await screen.findByText('Dashboard for member_one')
    expect(screen.queryByText(/Analyze mode/i)).toBeNull()
    expect(screen.queryByText(/Kill switch/i)).toBeNull()
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
    await waitFor(() => expect(document.querySelector('[data-family-shell]')).not.toBeNull())
  })
})
