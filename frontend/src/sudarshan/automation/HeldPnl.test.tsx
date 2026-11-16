/**
 * The automation card's P&L line (M12.1, GA10.4): the held contract's row from the book of the
 * strategy's own mode, moved by the books' own tick pipeline. "Real-time" only while ticks
 * arrive; otherwise the time the figure was last read. Read only.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import { STALE_MS } from '@/sudarshan/dashboard/live'
import { frames, manualClock } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { strategy } from './fixtures'
import { HeldPnl } from './HeldPnl'
import type { HeldBook } from './types'

const SYMBOL = 'NIFTY24NOV26FUT'
const READ = Date.UTC(2026, 10, 16, 4, 45, 2) // 10:15:02 in India
const MINUS = '−'

function held(book: Partial<HeldBook> | null, mode: 'sandbox' | 'live' = 'sandbox') {
  return strategy({
    mode,
    held_exchange: 'NFO',
    held_symbol: SYMBOL,
    held_side: 'long',
    held_book:
      book === null
        ? null
        : {
            quantity: 65,
            average_price: 25000,
            ltp: 25010,
            pnl: 650,
            today_realized_pnl: 0,
            priced_ms: READ,
            ...book,
          },
  })
}

function tick(ltp: number) {
  act(() => {
    feed.push(SYMBOL, 'NFO', { ltp })
    frames.flush()
  })
}

beforeEach(() => {
  manualClock()
  vi.setSystemTime(READ + 30_000)
  frames.install()
  feed.reset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the automation card’s P&L line', () => {
  it('says the book’s figure and when it was read until ticks arrive', () => {
    render(<HeldPnl strategy={held({})} />)
    const line = screen.getByTestId('auto-pnl')
    expect(line).toHaveTextContent('Long 65')
    expect(line).toHaveTextContent('+₹650.00')
    expect(line).toHaveTextContent('Read at 10:15:02')
    expect(line).not.toHaveTextContent('Real-time')
    expect(feed.live()).toEqual([`NFO:${SYMBOL}:LTP`])
  })

  it('moves with each tick and says Real-time only while they arrive', () => {
    render(<HeldPnl strategy={held({})} />)
    tick(25050)
    const line = screen.getByTestId('auto-pnl')
    // The book's P&L moved by the price since the read: 650 + 40 on 65.
    expect(line).toHaveTextContent('+₹3,250.00')
    expect(line).toHaveTextContent('Real-time')
    expect(line.querySelector('[data-direction="up"]')).not.toBeNull() // an arrow, not colour alone

    tick(24900)
    expect(line).toHaveTextContent(`${MINUS}₹6,500.00`)
    expect(line.querySelector('[data-direction="down"]')).not.toBeNull()

    act(() => {
      vi.advanceTimersByTime(STALE_MS + 1_000)
    })
    expect(line).not.toHaveTextContent('Real-time')
    expect(line).toHaveTextContent('Read at 10:15:32')
    expect(line).toHaveTextContent(`${MINUS}₹6,500.00`)
  })

  it('prices a short the other way', () => {
    render(<HeldPnl strategy={held({ quantity: -65, pnl: -650 })} />)
    tick(24990)
    const line = screen.getByTestId('auto-pnl')
    expect(line).toHaveTextContent('Short 65')
    expect(line).toHaveTextContent('+₹650.00') // −650 + 20 on a short 65
  })

  it('says when the book could not be read, and where it is read again', () => {
    render(<HeldPnl strategy={held(null)} />)
    expect(screen.getByTestId('auto-pnl')).toHaveTextContent(
      'The P&L could not be read from the Sandbox book. It is read again when you come back to this page.'
    )
    expect(feed.opened).toEqual([])
  })

  it('says the book shows nothing held, and subscribes to nothing', () => {
    render(<HeldPnl strategy={held({ quantity: 0, pnl: 120 })} />)
    expect(screen.getByTestId('auto-pnl')).toHaveTextContent('Not in the Sandbox book now.')
    render(<HeldPnl strategy={held({ quantity: 0 }, 'live')} />)
    expect(screen.getAllByTestId('auto-pnl')[1]).toHaveTextContent(
      "Not in Upstox's positions now. A new fill can take a minute to show there."
    )
    expect(feed.opened).toEqual([])
  })
})
