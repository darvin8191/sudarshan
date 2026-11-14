import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetInstrumentSessions } from '@/lib/trading/instrumentSession'
import { SettlementLine, settlementDay, settlementText } from './SettlementLine'

const asked: string[] = []
const facts: Record<string, string | null> = {}

beforeEach(() => {
  asked.length = 0
  resetInstrumentSessions()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      asked.push(url)
      const symbol = new URL(url, 'http://x').searchParams.get('symbol') ?? ''
      return {
        ok: true,
        json: async () => ({
          status: 'success',
          data: {
            day: '2026-11-23',
            stops_at: null,
            closing: null,
            delivery: facts[symbol] ?? null,
          },
        }),
      }
    })
  )
})

afterEach(() => vi.unstubAllGlobals())

describe('the delivery-settlement line (B7)', () => {
  it('words the day as a trader reads it', () => {
    expect(settlementDay('2026-11-26')).toBe('Thu 26 Nov')
    expect(settlementText('2026-12-29')).toBe(
      'Stock contract: settled by delivery if held to expiry on Tue 29 Dec.'
    )
  })

  it('shows for a stock contract in its expiry week, with an icon and the words', async () => {
    facts.RELIANCE26NOV261400CE = '2026-11-26'
    const { container } = render(<SettlementLine symbol="RELIANCE26NOV261400CE" exchange="NFO" />)
    await waitFor(() =>
      expect(
        screen.getByText('Stock contract: settled by delivery if held to expiry on Thu 26 Nov.')
      ).toBeInTheDocument()
    )
    expect(container.querySelector('[data-settlement] svg')).not.toBeNull()
  })

  it('shows nothing for an index contract, which the server never marks', async () => {
    const { container } = render(<SettlementLine symbol="NIFTY24NOV2625000CE" exchange="NFO" />)
    await waitFor(() => expect(asked).toHaveLength(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('asks nothing for a share, an index or a commodity', async () => {
    const { container } = render(
      <>
        <SettlementLine symbol="RELIANCE" exchange="NSE" />
        <SettlementLine symbol="NIFTY" exchange="NSE_INDEX" />
        <SettlementLine symbol="GOLDM05DEC26FUT" exchange="MCX" />
      </>
    )
    await Promise.resolve()
    expect(asked).toEqual([])
    expect(container).toBeEmptyDOMElement()
  })

  it('shows nothing when the facts cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')))
    const { container } = render(<SettlementLine symbol="RELIANCE26NOV261400CE" exchange="NFO" />)
    await Promise.resolve()
    expect(container).toBeEmptyDOMElement()
  })
})
