/**
 * SUDARSHAN M8.2: the watchlist's "Option chain" opens this page on the row's
 * underlying (/optionchain?exchange=NFO&underlying=SBIN). Without the link the
 * page keeps upstream's choice, the first default underlying of the exchange.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const chain = vi.hoisted(() => ({
  getUnderlyings: vi.fn(),
  getExpiries: vi.fn(),
}))

vi.mock('@/api/oi-profile', () => ({ oiProfileApi: chain }))
vi.mock('@/components/trading', () => ({ PlaceOrderDialog: () => null }))
vi.mock('@/hooks/useOptionChainLive', () => ({
  useOptionChainLive: () => ({
    data: null,
    isConnected: false,
    isStreaming: false,
    isPaused: false,
    error: null,
    lastUpdate: null,
    refetch: vi.fn(),
    isLoading: false,
    streamingSymbols: [],
  }),
}))

import OptionChain from './OptionChain'

function open(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OptionChain />
    </MemoryRouter>
  )
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

  it('keeps the default without a link', async () => {
    open('/optionchain')

    await waitFor(() => expect(chain.getExpiries).toHaveBeenCalledWith('NFO', 'NIFTY'))
    expect(underlying()).toHaveTextContent('NIFTY')
  })
})
