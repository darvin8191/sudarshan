/**
 * SUDARSHAN M8.2: the watchlist's "Option chain" opens this page on the row's
 * underlying (/optionchain?exchange=NFO&underlying=SBIN). Without the link the
 * page keeps upstream's choice, the first default underlying of the exchange.
 * The link is used once: it leaves the address as soon as it is read, and
 * changing the exchange spends it.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const chain = vi.hoisted(() => ({
  getUnderlyings: vi.fn(),
  getExpiries: vi.fn(),
}))

vi.mock('@/api/oi-profile', () => ({ oiProfileApi: chain }))
vi.mock('@/components/trading', () => ({ PlaceOrderDialog: () => null }))
/** One object, as the real hook keeps its answers stable between renders. */
const live = vi.hoisted(() => ({
  data: null,
  isConnected: false,
  isStreaming: false,
  isPaused: false,
  error: null,
  lastUpdate: null,
  refetch: () => {},
  isLoading: false,
  streamingSymbols: 0,
  // The books' status line (M3.7) reads where the chain's feed stands.
  tickDirections: new Map(),
  feed: { lastTickAt: null, lastUpdateAt: null, due: 0, connected: false },
}))
vi.mock('@/hooks/useOptionChainLive', () => ({ useOptionChainLive: () => live }))

import OptionChain from './OptionChain'

function Where() {
  const location = useLocation()
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>
}

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OptionChain />
      <Where />
    </MemoryRouter>
  )
}

/** The exchange picker: the combobox that shows the exchange. */
const exchange = () => {
  const picker = screen
    .getAllByRole('combobox')
    .find((box) => /^(NFO|BFO|MCX)$/.test(box.textContent?.trim() ?? ''))
  if (!picker) throw new Error('no exchange picker')
  return picker
}

/** The underlying picker: the one combobox that opens a popover rather than a list box. */
const underlying = () => {
  const picker = screen
    .getAllByRole('combobox')
    .find((box) => box.getAttribute('aria-haspopup') === 'dialog')
  if (!picker) throw new Error('no underlying picker')
  return picker
}

beforeEach(() => {
  localStorage.clear()
  chain.getUnderlyings.mockReset()
  chain.getUnderlyings.mockImplementation(async (exchange: string) => ({
    status: 'success',
    underlyings: exchange === 'BFO' ? ['SENSEX', 'BANKEX'] : ['NIFTY', 'BANKNIFTY', 'SBIN'],
  }))
  chain.getExpiries.mockReset()
  chain.getExpiries.mockResolvedValue({ status: 'success', expiries: ['28-OCT-26'] })
})

describe('OptionChain opened from a link', () => {
  it('opens on the underlying the link names', async () => {
    open('/optionchain?exchange=NFO&underlying=SBIN')

    await waitFor(() => expect(chain.getExpiries).toHaveBeenCalledWith('NFO', 'SBIN'))
    expect(underlying()).toHaveTextContent('SBIN')
    // The page reads the stored preference's expiries on its first pass, as it
    // always has, and drops that answer; the last read is the linked one.
    expect(chain.getExpiries).toHaveBeenLastCalledWith('NFO', 'SBIN')
  })

  it('opens on the exchange the link names', async () => {
    open('/optionchain?exchange=BFO&underlying=SENSEX')

    await waitFor(() => expect(chain.getExpiries).toHaveBeenCalledWith('BFO', 'SENSEX'))
    expect(chain.getUnderlyings).toHaveBeenCalledWith('BFO')
  })

  it("falls back to the exchange's default for an underlying it does not list", async () => {
    open('/optionchain?exchange=NFO&underlying=NOSUCH')

    await waitFor(() => expect(underlying()).toHaveTextContent('NIFTY'))
  })

  it('takes the link out of the address once read, so a refresh opens the page as usual', async () => {
    open('/optionchain?exchange=NFO&underlying=SBIN')

    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/optionchain$/))
    await waitFor(() => expect(underlying()).toHaveTextContent('SBIN'))
  })

  it('spends the link once the exchange is changed', async () => {
    const user = userEvent.setup()
    open('/optionchain?exchange=NFO&underlying=SBIN')
    await waitFor(() => expect(underlying()).toHaveTextContent('SBIN'))

    await user.click(exchange())
    await user.click(await screen.findByRole('option', { name: 'BFO' }))
    await waitFor(() => expect(underlying()).toHaveTextContent('SENSEX'))

    // Back on the linked exchange: its default, not the link's underlying.
    await user.click(exchange())
    await user.click(await screen.findByRole('option', { name: 'NFO' }))
    await waitFor(() => expect(underlying()).toHaveTextContent('NIFTY'))
    expect(chain.getExpiries).toHaveBeenLastCalledWith('NFO', 'NIFTY')
  })

  it('keeps the default without a link', async () => {
    open('/optionchain')

    await waitFor(() => expect(chain.getExpiries).toHaveBeenCalledWith('NFO', 'NIFTY'))
    expect(underlying()).toHaveTextContent('NIFTY')
  })
})
