/**
 * The quote's status line in the order ticket and the modify dialog: a
 * contract nobody has traded today has no price to be late with, so it says
 * "No trade yet" rather than "Stale".
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { resetInstrumentSessions } from '@/lib/trading/instrumentSession'

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

/**
 * SUDARSHAN review M3: from 7 Dec 2026 an expiring index contract stops at
 * 15:30 on its expiry day while F&O trades to 15:45. The order dialogs said
 * its quote was old; they now say it stopped trading, from the server's stop.
 */
describe('QuoteStatus for an expiring contract (review M3)', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    resetInstrumentSessions()
  })

  const stopsAt = Date.UTC(2026, 11, 8, 10, 0) // 15:30 in India on Tue 8 Dec 2026
  function serve(stops: number | null) {
    resetInstrumentSessions()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          status: 'success',
          data: { day: '2026-12-08', stops_at: stops, closing: null },
        }),
      }))
    )
  }
  const stale = (now: number) => ({
    lastTickAt: null,
    lastUpdateAt: now - 30_000,
    due: 1,
    connected: true,
    noTrade: false,
  })

  it('says the contract stopped trading at 15:30, never Stale, once its stop has passed', async () => {
    vi.useFakeTimers({ now: stopsAt + 35_000, toFake: ['Date'] })
    serve(stopsAt)
    render(
      <QuoteStatus
        exchange="NFO"
        symbol="NIFTY08DEC2625900CE"
        calendar={open}
        feed={stale(Date.now())}
      />
    )
    expect(await screen.findByText('Stopped trading at 15:30')).toBeInTheDocument()
    expect(screen.queryByText(/Stale/)).toBeNull()
  })

  it('reads as before while the contract still trades, and for a contract without a stop', async () => {
    vi.useFakeTimers({ now: stopsAt - 60_000, toFake: ['Date'] })
    serve(stopsAt)
    render(
      <QuoteStatus
        exchange="NFO"
        symbol="NIFTY08DEC2625900CE"
        calendar={open}
        feed={stale(Date.now())}
      />
    )
    expect(await screen.findByText(/Stale/)).toBeInTheDocument()
    expect(screen.queryByText(/Stopped trading/)).toBeNull()
  })

  it('asks again for the new day when the dialog is left open past midnight (review N3)', async () => {
    const monday = Date.UTC(2026, 11, 7, 18, 29, 50) // 23:59:50 in India
    vi.useFakeTimers({
      now: monday,
      shouldAdvanceTime: false,
      toFake: ['setTimeout', 'clearTimeout', 'Date'],
    })
    resetInstrumentSessions()
    const asked = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        asked()
        const tuesday = Date.now() >= Date.UTC(2026, 11, 7, 18, 30)
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'success',
            data: { day: 'd', stops_at: tuesday ? stopsAt : null, closing: null },
          }),
        }
      })
    )
    const view = render(
      <QuoteStatus
        exchange="NFO"
        symbol="NIFTY08DEC2625900CE"
        calendar={open}
        feed={stale(Date.now())}
      />
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(asked).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000) // 00:00:00
    })
    expect(asked).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(asked).toHaveBeenCalledTimes(2)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
