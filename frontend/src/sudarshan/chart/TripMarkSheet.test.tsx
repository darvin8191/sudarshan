/**
 * The sheet a tap on a trade's mark opens (M14.8, review L4): every fill of the mark in full,
 * since the mark itself carries only its letter on narrow candles. Every value is synthetic.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TripMarkSheet } from './TripMarkSheet'
import type { MarkLeg } from './tripMarkers'

const at = (h: number, m: number, s: number) => Date.UTC(2026, 10, 16, h, m, s) - 19_800_000
const fill = (action: MarkLeg['action'], qty: number, price: number, ms: number): MarkLeg => ({
  role: 'entry',
  action,
  qty,
  price_u: Math.round(price * 10_000),
  exch_ts_ms: ms,
})

describe('the fills of a mark', () => {
  it('writes each fill with its time in India, its side in words, its quantity and its price', () => {
    render(
      <TripMarkSheet
        fills={[fill('BUY', 65, 803.05, at(10, 2, 5)), fill('BUY', 130, 803.4, at(10, 2, 41))]}
        decimals={2}
        onOpenChange={vi.fn()}
      />
    )
    expect(screen.getByText('Entry: bought at this candle')).toBeInTheDocument()
    const rows = within(screen.getByTestId('trip-mark-fills')).getAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual([
      '10:02:05Bought65 at 803.05',
      '10:02:41Bought130 at 803.40',
    ])
  })

  it('names a sale in words, never by colour alone', () => {
    render(
      <TripMarkSheet
        fills={[{ ...fill('SELL', 65, 809.4, at(10, 41, 50)), role: 'exit' }]}
        decimals={2}
        onOpenChange={vi.fn()}
      />
    )
    expect(screen.getByText('Exit: sold at this candle')).toBeInTheDocument()
    expect(screen.getByText('Sold')).toBeInTheDocument()
  })

  it('stays shut without fills', () => {
    render(<TripMarkSheet fills={null} decimals={2} onOpenChange={vi.fn()} />)
    expect(screen.queryByTestId('trip-mark-fills')).toBeNull()
  })
})
