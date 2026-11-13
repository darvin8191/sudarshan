import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FamilyBadge } from './FamilyBadge'
import { FamilyLayout } from './FamilyLayout'
import { resetFamilyManagerForTests } from './familyManager'
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

function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/family/login" element={<p>The sign-in page</p>} />
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
