/**
 * The quote's status line in the order ticket and the modify dialog: a
 * contract nobody has traded today has no price to be late with, so it says
 * "No trade yet" rather than "Stale".
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import { QuoteStatus } from './QuoteStatus'

const open = {
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
  isLoading: false,
  error: null,
}

describe('QuoteStatus', () => {
  it('says "No trade yet" for a contract with no tick and no volume today', () => {
    render(
      <QuoteStatus
        exchange="NFO"
        calendar={open}
        feed={{
          lastTickAt: null,
          lastUpdateAt: Date.now() - 30_000,
          due: 1,
          connected: true,
          noTrade: true,
        }}
      />
    )
    expect(screen.getByText('No trade yet')).toBeInTheDocument()
    expect(screen.queryByText(/Stale/)).toBeNull()
  })

  it('still says Stale for a contract that has traded and whose figures have stopped', () => {
    render(
      <QuoteStatus
        exchange="NFO"
        calendar={open}
        feed={{
          lastTickAt: null,
          lastUpdateAt: Date.now() - 30_000,
          due: 1,
          connected: true,
          noTrade: false,
        }}
      />
    )
    expect(screen.getByText(/Stale/)).toBeInTheDocument()
  })
})
