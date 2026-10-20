import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { INDEX_REFRESH_MS, IndexStrip } from './IndexStrip'

const getMultiQuotes = vi.fn()
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: (...args: unknown[]) => getMultiQuotes(...args) },
}))

let nseOpen = false
vi.mock('@/hooks/useMarketStatus', () => ({
  useMarketStatus: () => ({ isMarketOpen: () => nseOpen }),
}))

const MINUS = '−'

function quote(symbol: string, ltp: number, prev_close: number) {
  return {
    symbol,
    exchange: 'NSE_INDEX',
    data: { ltp, prev_close, open: 0, high: 0, low: 0, volume: 0, oi: 0, bid: 0, ask: 0 },
  }
}

const ANSWER = {
  status: 'success',
  results: [
    quote('NIFTY', 25874.3, 25714.85),
    quote('BANKNIFTY', 57912.15, 58016.6),
    quote('INDIAVIX', 11.42, 11.79),
  ],
}

/** Let the quote call's promise settle inside React's act. */
async function settle() {
  await act(async () => {
    await Promise.resolve()
  })
}

beforeEach(() => {
  nseOpen = false
  getMultiQuotes.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('IndexStrip', () => {
  it('shows NIFTY 50, NIFTY BANK and INDIA VIX with the change on the previous close', async () => {
    getMultiQuotes.mockResolvedValue(ANSWER)
    render(<IndexStrip apiKey="key" />)
    await settle()

    const tiles = screen.getAllByRole('listitem')
    expect(tiles.map((tile) => tile.textContent)).toEqual([
      'NIFTY 5025,874.30+0.62%',
      `NIFTY BANK57,912.15${MINUS}0.18%`,
      `INDIA VIX11.42${MINUS}3.14%`,
    ])
    expect(tiles[0].querySelector('[data-direction]')).toHaveAttribute('data-direction', 'up')
    expect(tiles[1].querySelector('[data-direction]')).toHaveAttribute('data-direction', 'down')
    expect(getMultiQuotes).toHaveBeenCalledWith('key', [
      { symbol: 'NIFTY', exchange: 'NSE_INDEX' },
      { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX' },
      { symbol: 'INDIAVIX', exchange: 'NSE_INDEX' },
    ])
  })

  it('asks nothing and shows nothing without an API key', async () => {
    const { container } = render(<IndexStrip apiKey={null} />)
    await settle()

    expect(container).toBeEmptyDOMElement()
    expect(getMultiQuotes).not.toHaveBeenCalled()
  })

  it('shows no change when the previous close is missing, rather than a wrong one', async () => {
    getMultiQuotes.mockResolvedValue({ status: 'success', results: [quote('NIFTY', 25874.3, 0)] })
    render(<IndexStrip apiKey="key" />)
    await settle()

    const [nifty, bank] = screen.getAllByRole('listitem')
    expect(nifty).toHaveTextContent(/^NIFTY 5025,874\.30$/)
    expect(bank).toHaveTextContent('Not available')
  })

  it('says so when the quotes cannot be read', async () => {
    getMultiQuotes.mockRejectedValue(new Error('network'))
    render(<IndexStrip apiKey="key" />)
    await settle()

    expect(screen.getByText('Index prices are not available right now.')).toBeInTheDocument()
    expect(screen.getAllByText('Not available')).toHaveLength(3)
  })

  it('reads again every 30 seconds while NSE is open, and not while it is closed', async () => {
    vi.useFakeTimers()
    getMultiQuotes.mockResolvedValue(ANSWER)
    render(<IndexStrip apiKey="key" />)
    await settle()
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(INDEX_REFRESH_MS * 3)
    })
    expect(getMultiQuotes).toHaveBeenCalledTimes(1)

    nseOpen = true
    await act(async () => {
      await vi.advanceTimersByTimeAsync(INDEX_REFRESH_MS)
    })
    expect(getMultiQuotes).toHaveBeenCalledTimes(2)
  })
})
