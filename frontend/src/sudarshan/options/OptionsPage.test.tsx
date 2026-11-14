/**
 * The options page on the market-data harness: the bridge's reads answered by the test, real
 * subscriptions, ticks pushed by hand.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  readListing: vi.fn(),
  readChain: vi.fn(),
  readFallback: vi.fn(),
}))
const calendar = vi.hoisted(() => {
  const state = { open: true as boolean }
  return {
    state,
    value: {
      isLoading: false,
      error: null,
      isMarketOpen: () => state.open,
      isAnyMarketOpen: () => state.open,
    },
  }
})
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar.value }))
vi.mock('@/components/auth/AuthSync', () => ({ syncSession: vi.fn(async () => {}) }))
vi.mock('@/api/client', () => ({
  webClient: {
    get: vi.fn(async (_url: string, config: { params: { exchange: string } }) => ({
      data: {
        status: 'success',
        underlyings:
          config.params.exchange === 'NFO' ? ['NIFTY', 'RELIANCE', 'RELINFRA'] : ['SENSEX'],
      },
    })),
  },
  apiClient: {},
}))

import { useAuthStore } from '@/stores/authStore'
import { frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { readChain, readFallback, readListing } from './api'
import { chainOf, EXPIRY, LISTING, NOW, symbolOf } from './fixtures'
import OptionsPage, { LOGIN_NEEDED } from './OptionsPage'
import { readLastChain, saveLastChain } from './prefs'

const listingRead = vi.mocked(readListing)
const chainRead = vi.mocked(readChain)

function Where() {
  const location = useLocation()
  return <output data-testid="where">{location.pathname + location.search}</output>
}

async function open(path = '/options/NFO/NIFTY') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/options" element={<OptionsPage />} />
          <Route path="/options/:exchange/:underlying" element={<OptionsPage />} />
          <Route path="*" element={<p>another page</p>} />
        </Routes>
        <Where />
      </MemoryRouter>
    </QueryClientProvider>
  )
  await settle()
  return view
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  act(() => frames.flush())
}

const where = () => screen.getByTestId('where').textContent
const state = () =>
  document.querySelector('[data-options-state]')?.getAttribute('data-options-state')

beforeEach(() => {
  localStorage.clear()
  feed.reset()
  frames.install()
  vi.spyOn(Date, 'now').mockReturnValue(NOW + 2000)
  calendar.state.open = true
  useAuthStore.setState({ apiKey: 'test-key', sessionChecked: true, brokerSessionExpired: false })
  listingRead.mockReset().mockResolvedValue({ state: 'ok', words: null, listing: LISTING })
  chainRead.mockReset().mockResolvedValue({ state: 'ok', words: null, chain: chainOf() })
  vi.mocked(readFallback).mockReset().mockResolvedValue(null)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('the options page', () => {
  it('opens NIFTY’s nearest expiry the first time, from a bare /options', async () => {
    await open('/options')
    expect(where()).toBe('/options/NFO/NIFTY')
    expect(screen.getByRole('heading', { level: 1, name: 'NIFTY' })).toBeInTheDocument()
    expect(listingRead.mock.calls[0].slice(0, 2)).toEqual(['NFO', 'NIFTY'])
    expect(chainRead.mock.calls[0].slice(0, 3)).toEqual(['NFO', 'NIFTY', EXPIRY])
    expect(readLastChain()).toEqual({ exchange: 'NFO', underlying: 'NIFTY', expiry: EXPIRY })
  })

  it('opens the underlying and expiry last used on this phone', async () => {
    saveLastChain({ exchange: 'NFO', underlying: 'NIFTY', expiry: '2026-11-24' })
    await open('/options')
    expect(where()).toBe('/options/NFO/NIFTY?expiry=2026-11-24')
    expect(chainRead.mock.calls[0][2]).toBe('2026-11-24')
  })

  it('takes the underlying’s own exchange in the address, and an expiry the listing lacks falls to the nearest', async () => {
    await open('/options/NSE_INDEX/nifty?expiry=2020-01-01')
    expect(listingRead.mock.calls[0].slice(0, 2)).toEqual(['NFO', 'NIFTY'])
    expect(chainRead.mock.calls[0][2]).toBe(EXPIRY)
  })

  it('shows the header strip: spot and change, the future’s basis, ATM IV, PCR, max pain, Real-time', async () => {
    await open()
    act(() => {
      feed.push('NIFTY', 'NSE_INDEX', { ltp: 25_920, prev_close: 25_800 }, 'websocket', NOW + 1500)
      feed.push('NIFTY24NOV26FUT', 'NFO', { ltp: 25_968 }, 'websocket', NOW + 1500)
      feed.push(symbolOf(25_900, 'CE'), 'NFO', { ltp: 150 }, 'websocket', NOW + 1500)
    })
    act(() => frames.flush())
    const spot = document.querySelector('[data-spot]') as HTMLElement
    expect(spot).toHaveTextContent('25,920.00')
    expect(spot).toHaveTextContent('+0.47%')
    expect(spot.querySelector('[data-direction="up"]')).not.toBeNull()
    expect(spot).toHaveTextContent('Fut 25,968.00 (+48.00)')
    const facts = document.querySelector('[data-chain-facts]') as HTMLElement
    expect(facts.textContent).toMatch(/ATM IV\d+\.\d/)
    expect(facts.textContent).toMatch(/PCR\d\.\d\d/)
    expect(facts.textContent).toMatch(/Max pain\d\d,\d{3}/)
    expect(screen.getByText('Real-time')).toBeInTheDocument()
    expect(document.querySelector('[data-wings]')).toBeNull() // read two seconds ago
  })

  it('says the far strikes’ time once the snapshot is older than ninety seconds', async () => {
    chainRead.mockResolvedValue({
      state: 'ok',
      words: null,
      chain: chainOf({ asOf: NOW - 120_000 }),
    })
    await open()
    expect(document.querySelector('[data-wings]')).toHaveTextContent('far strikes as of 10:40:05')
  })

  it('never says Real-time without ticks: the market closed says so', async () => {
    calendar.state.open = false
    await open()
    expect(screen.queryByText('Real-time')).toBeNull()
    expect(screen.getByText('Market closed')).toBeInTheDocument()
  })

  it('says Reconnecting with the figures’ age when the socket drops', async () => {
    await open()
    act(() => feed.push('NIFTY', 'NSE_INDEX', { ltp: 25_920 }, 'websocket', NOW + 1500))
    act(() => frames.flush())
    act(() =>
      feed.setState({ connectionState: 'disconnected', isConnected: false, isAuthenticated: false })
    )
    expect(screen.queryByText('Real-time')).toBeNull()
    expect(screen.getByText(/^Reconnecting · updated/)).toBeInTheDocument()
  })

  it('names each expiry with its days left, marks the chosen one, and reads the one tapped', async () => {
    await open()
    const chips = within(screen.getByRole('list', { name: 'Expiry' })).getAllByRole('button')
    expect(chips.map((chip) => chip.textContent)).toEqual([
      '17 Nov · 1d',
      '24 Nov · 8d',
      '29 Dec · 43d',
    ])
    expect(chips[0]).toHaveAttribute('aria-pressed', 'true')
    expect(chips[1]).toHaveAccessibleName('24 Nov, 8 days left')
    fireEvent.click(chips[1])
    await settle()
    expect(where()).toBe('/options/NFO/NIFTY?expiry=2026-11-24')
    expect(chainRead).toHaveBeenLastCalledWith('NFO', 'NIFTY', '2026-11-24', expect.anything())
    expect(readLastChain().expiry).toBe('2026-11-24')
  })

  it('says "today" on the expiry day', async () => {
    vi.mocked(Date.now).mockReturnValue(NOW + 86_400_000)
    await open()
    expect(screen.getByRole('button', { name: '17 Nov, expires today' })).toHaveTextContent(
      '17 Nov · today'
    )
  })

  it('picks another underlying: the indices first, then a search over the master', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: /Change the underlying/ }))
    const sheet = screen.getByRole('dialog', { name: 'Underlying' })
    expect(
      within(sheet)
        .getAllByRole('button')
        .slice(1, 7)
        .map((each) => each.textContent)
    ).toEqual([
      'NIFTYNFO · open',
      'BANKNIFTYNFO',
      'FINNIFTYNFO',
      'MIDCPNIFTYNFO',
      'SENSEXBFO',
      'BANKEXBFO',
    ]) // prettier-ignore
    fireEvent.change(within(sheet).getByRole('searchbox'), { target: { value: 'rel' } })
    await waitFor(() =>
      expect(within(sheet).getByRole('button', { name: /RELIANCE/ })).toBeInTheDocument()
    )
    expect(within(sheet).getByRole('link', { name: /MCX options/ })).toHaveAttribute(
      'href',
      '/optionchain'
    )
    fireEvent.click(within(sheet).getByRole('button', { name: /RELIANCE/ }))
    await settle()
    expect(where()).toBe('/options/NFO/RELIANCE')
  })

  it('with today’s Upstox login lapsed, draws nothing as current and offers Reconnect', async () => {
    useAuthStore.setState({ apiKey: null, sessionChecked: true, brokerSessionExpired: true })
    await open()
    expect(state()).toBe('signin')
    expect(screen.getByText(LOGIN_NEEDED)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Reconnect' })).toHaveAttribute('href', '/broker')
    expect(document.querySelector('[data-spot]')).toBeNull()
    expect(document.querySelector('[data-chain-facts]')).toBeNull()
    expect(screen.queryByText('Real-time')).toBeNull()
    expect(chainRead).not.toHaveBeenCalled()
    expect(feed.live()).toEqual([])
  })

  it('shows the same card with the server’s sentence when Upstox refuses the token', async () => {
    chainRead.mockResolvedValue({ state: 'signin', words: LOGIN_NEEDED, chain: null })
    await open()
    expect(state()).toBe('signin')
    expect(document.querySelector('[data-spot]')).toBeNull()
  })

  it('keeps the chain when a read fails, with the sentence, the time of the open interest and Try again', async () => {
    await open()
    chainRead.mockResolvedValue({
      state: 'unavailable',
      words: 'The option chain could not be read.',
      chain: null,
    })
    vi.mocked(Date.now).mockReturnValue(NOW + 10_000)
    fireEvent.click(screen.getByRole('button', { name: 'Read the option chain again' }))
    await settle()
    expect(state()).toBe('unavailable')
    expect(
      screen.getByText(
        'The option chain could not be read. Prices keep moving; open interest is as of 10:42:05.'
      )
    ).toBeInTheDocument()
    expect(document.querySelector('[data-chain-facts]')).not.toBeNull()
    chainRead.mockResolvedValue({
      state: 'ok',
      words: null,
      chain: chainOf({ asOf: NOW + 16_000 }),
    })
    vi.mocked(Date.now).mockReturnValue(NOW + 16_000)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await settle()
    expect(state()).toBeUndefined()
  })

  it('says so for an underlying with no options, with the wide chain one tap away', async () => {
    listingRead.mockResolvedValue({
      state: 'none',
      words: 'No options that still trade are listed for ABC.',
      listing: null,
    })
    await open('/options/NFO/ABC')
    expect(state()).toBe('none')
    expect(screen.getByText('No options that still trade are listed for ABC.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the wide option chain' })).toHaveAttribute(
      'href',
      '/optionchain'
    )
    expect(screen.getByRole('button', { name: 'Pick another underlying' })).toBeInTheDocument()
    expect(chainRead).not.toHaveBeenCalled()
  })

  it('does not ask the bridge for MCX: it points to the wide option chain', async () => {
    await open('/options/MCX/CRUDEOIL')
    expect(state()).toBe('none')
    expect(screen.getByText(/Upstox has no option chain for MCX/)).toBeInTheDocument()
    expect(listingRead).not.toHaveBeenCalled()
  })

  it('offers Try again when the expiries cannot be read', async () => {
    listingRead.mockResolvedValueOnce({
      state: 'unavailable',
      words: 'The expiries could not be read.',
      listing: null,
    })
    await open()
    expect(state()).toBe('unavailable')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await settle()
    expect(screen.getByRole('heading', { level: 1, name: 'NIFTY' })).toBeInTheDocument()
  })

  it('lets every subscription go when the page is left', async () => {
    const view = await open()
    expect(feed.live()).toHaveLength(84)
    view.unmount()
    expect(feed.live()).toEqual([])
  })
})
