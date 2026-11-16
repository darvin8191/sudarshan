/**
 * M13.9 (U6): "Your positions" against TRINETRA's regime, from the position book the app already
 * reads (OpenAlgo's /api/v1/positionbook, read by the books' rules). Per index: where the owner
 * stands, the regime's bias beside it, and "With the regime" or "Against the regime" with its icon.
 * The regime readings are the real service's (bridge.answers.json): NIFTY bearish, BANKNIFTY
 * neutral. The positions are invented.
 */
import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getPositions = vi.hoisted(() => vi.fn())
vi.mock('@/api/trading', () => ({ tradingApi: { getPositions } }))
const access = vi.hoisted(() => ({
  current: { state: 'ready', apiKey: 'key' } as Record<string, unknown>,
}))
vi.mock('@/sudarshan/access', () => ({ usePriceAccess: () => access.current }))
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket: null }),
}))
/** One object, as the real hook keeps its answers stable between renders. */
const calendar = vi.hoisted(() => ({
  isLoading: false,
  error: null,
  isMarketOpen: () => true,
  isAnyMarketOpen: () => true,
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar }))

import { LATER_MS, NOW_MS, stateOf } from '@/test/signalsAnswers'
import { renderSignals } from '@/test/signalsRender'
import { PositionsCard } from './PositionsCard'
import { freshnessAt } from './words'

const book = (rows: Array<{ symbol: string; quantity: number }>) => ({
  status: 'success',
  data: rows.map((row) => ({
    ...row,
    exchange: 'NFO',
    product: 'NRML',
    average_price: 100,
    ltp: 101,
    pnl: 0,
    pnlpercent: 0,
  })),
})

function show(name: 'state' | 'state_stale' = 'state', now = NOW_MS) {
  const state = stateOf(name)
  return renderSignals(
    <PositionsCard state={state} fresh={freshnessAt(state.freshness, now)} now={now} />
  )
}

beforeEach(() => {
  getPositions.mockReset()
  access.current = { state: 'ready', apiKey: 'key' }
})

afterEach(() => vi.useRealTimers())

describe('your positions against the regime', () => {
  it('long NIFTY through calls bought and puts sold, against a bearish regime', async () => {
    getPositions.mockResolvedValue(
      book([
        { symbol: 'NIFTY17NOV2625900CE', quantity: 130 },
        { symbol: 'NIFTY17NOV2625700PE', quantity: -65 },
        { symbol: 'BANKNIFTY25NOV26FUT', quantity: -30 },
      ])
    )
    show()
    const nifty = await screen.findByText('You are long NIFTY.')
    const row = nifty.closest('li') as HTMLElement
    expect(row).toHaveAttribute('data-stance', 'against')
    expect(row).toHaveTextContent('(calls bought, puts sold)')
    expect(row).toHaveTextContent('The regime readsbearish(FII distribution, confidence 0.71)')
    const against = within(row).getByText('Against the regime').closest('p') as HTMLElement
    expect(against.querySelector('svg')).not.toBeNull()
    // Review L5: a reading, never advice. Plain ink and a neutral mark, as "With the regime" has.
    expect(against.className).toContain('var(--text-primary)')
    expect(against.className).not.toContain('warning')
    expect(against.querySelector('svg')?.getAttribute('class')).not.toContain('triangle-alert')
    expect(getPositions).toHaveBeenCalledWith('key')

    const bank = screen.getByText('You are short BANKNIFTY.').closest('li') as HTMLElement
    expect(bank).toHaveAttribute('data-stance', 'neutral')
    expect(bank).toHaveTextContent('The regime reads neutral')
  })

  it('short NIFTY through a future sold: with the regime', async () => {
    getPositions.mockResolvedValue(book([{ symbol: 'NIFTY25NOV26FUT', quantity: -65 }]))
    show()
    const row = (await screen.findByText('You are short NIFTY.')).closest('li') as HTMLElement
    expect(row).toHaveAttribute('data-stance', 'with')
    expect(row).toHaveTextContent('With the regime')
  })

  it('a spread is mixed, sizes not weighed; nothing open says so', async () => {
    getPositions.mockResolvedValue(
      book([
        { symbol: 'NIFTY17NOV2625900CE', quantity: 650 },
        { symbol: 'NIFTY17NOV2626100CE', quantity: -65 },
      ])
    )
    show()
    const row = (await screen.findByText('You hold long and short NIFTY positions.')).closest(
      'li'
    ) as HTMLElement
    expect(row).toHaveAttribute('data-stance', 'mixed')
    const bank = screen
      .getByText('No open BANKNIFTY futures or options.')
      .closest('li') as HTMLElement
    expect(bank).toHaveAttribute('data-stance', 'none')
  })

  it('stale: the stance is kept, and says which reading it was read against', async () => {
    getPositions.mockResolvedValue(book([{ symbol: 'NIFTY25NOV26FUT', quantity: 65 }]))
    show('state_stale', LATER_MS)
    const row = (await screen.findByText('You are long NIFTY.')).closest('li') as HTMLElement
    expect(row).toHaveTextContent("Compared with TRINETRA's last reading, 15 minutes old.")
  })

  it('no regime reading to compare with', async () => {
    getPositions.mockResolvedValue(book([{ symbol: 'NIFTY25NOV26FUT', quantity: 65 }]))
    const state = stateOf()
    state.readings = state.readings.filter((reading) => reading.module !== 'regime')
    renderSignals(
      <PositionsCard state={state} fresh={freshnessAt(state.freshness, NOW_MS)} now={NOW_MS} />
    )
    const row = (await screen.findByText('You are long NIFTY.')).closest('li') as HTMLElement
    expect(row).toHaveAttribute('data-stance', 'unknown')
    expect(row).toHaveTextContent('TRINETRA has no current regime reading for NIFTY.')
  })

  it("without today's Upstox login: the app's own notice, and nothing read", () => {
    access.current = { state: 'upstox-logged-out', apiKey: null }
    show()
    expect(screen.getByText(/Upstox is not logged in today/)).toBeInTheDocument()
    expect(getPositions).not.toHaveBeenCalled()
  })

  it('a book that cannot be read is said, with when it is read again', async () => {
    getPositions.mockRejectedValue(new Error('Network Error'))
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your positions could not be read just now.'
    )
    await waitFor(() => expect(screen.queryByText(/You are/)).toBeNull())
  })
})
