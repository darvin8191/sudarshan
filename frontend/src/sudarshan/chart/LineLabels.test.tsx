import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { positionPnlAt } from '@/hooks/useLivePrice'
import { priceRow } from '@/sudarshan/books/prices'
import type { Position } from '@/types/trading'
import { LineLabels, positionPnl } from './LineLabels'
import type { ChartLine, LineY } from './overlays'
import type { PhoneChart } from './phoneChart'
import { PriceStore } from './prices'

const held: Position = {
  symbol: 'NIFTY18NOV2625900CE',
  exchange: 'NFO',
  product: 'MIS',
  quantity: 65,
  average_price: 131.2,
  ltp: 140,
  // Rs 150 was booked earlier today on a part close: the broker's figure carries it.
  pnl: 722,
  pnlpercent: 8.47,
}
const BOOK_AT = 1_000_000

const line = (over: Partial<ChartLine> = {}): ChartLine => ({
  id: 'position:MIS',
  kind: 'position',
  price: 131.2,
  side: 'BUY',
  label: 'Buy 65 @ 131.20',
  ref: { kind: 'position', position: held },
  ...over,
})

function fakeOwner(ys: LineY[]) {
  const listeners = new Set<() => void>()
  let layout = ys
  return {
    owner: {
      overlayLayout: () => layout,
      onOverlayLayout: (listener: () => void) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
    } as unknown as PhoneChart,
    move(next: LineY[]) {
      layout = next
      for (const listener of listeners) listener()
    },
  }
}

const now = (run: () => void) => run()

describe("a position line's P&L", () => {
  it("is the figure the Positions screen works out: the broker's, moved by the price since the book", () => {
    const tick = { ltp: 142.35, at: BOOK_AT + 5_000, polled: false }
    const positions = priceRow(
      held,
      {
        tick: {
          symbol: held.symbol,
          exchange: held.exchange,
          data: { ltp: tick.ltp },
          lastUpdate: tick.at,
          updateSource: 'websocket',
        },
        bookAt: BOOK_AT,
      },
      positionPnlAt
    ).pnl
    expect(positionPnl(held, BOOK_AT, tick)).toBe(positions)
    // 722 at 140.00, and 65 x 2.35 more at 142.35: not (142.35 - 131.20) x 65 = 724.75.
    expect(positionPnl(held, BOOK_AT, tick)).toBeCloseTo(874.75, 2)
  })

  it("stays the broker's own figure before a tick, and for a quote older than the book", () => {
    expect(positionPnl(held, BOOK_AT, null)).toBe(722)
    expect(positionPnl(held, BOOK_AT, { ltp: 150, at: BOOK_AT - 1, polled: true })).toBe(722)
  })

  it('moves on every tick, with its sign and arrow, and is grey while the price is not real-time', () => {
    const prices = new PriceStore(now)
    const { owner } = fakeOwner([{ id: 'position:MIS', y: 200 }])
    const view = (quiet: boolean) => (
      <LineLabels
        owner={owner}
        lines={[line()]}
        more={null}
        bookAt={BOOK_AT}
        prices={prices}
        quiet={quiet}
      />
    )
    const { rerender } = render(view(false))
    const label = screen.getByText('Buy 65 @ 131.20').closest('[data-line]') as HTMLElement
    expect(label.textContent).toContain('+₹722.00')
    act(() =>
      prices.set({ ltp: 142.35, time: null, tradeTime: null, at: BOOK_AT + 5_000, polled: false })
    )
    expect(label.textContent).toContain('+₹874.75')
    expect(label.querySelector('[data-direction="up"]')).not.toBeNull()
    act(() =>
      prices.set({ ltp: 120, time: null, tradeTime: null, at: BOOK_AT + 6_000, polled: false })
    )
    expect(label.textContent).toContain('−₹578.00')
    expect(label.querySelector('svg[data-direction="down"]')).not.toBeNull()
    rerender(view(true))
    expect(label.querySelector('[data-stale]')).not.toBeNull()
  })
})

describe('the labels over the chart', () => {
  const prices = new PriceStore(now)
  const order = line({
    id: 'order:A1:trigger',
    kind: 'order',
    side: 'SELL',
    price: 120,
    label: 'SELL 65 SL-M 120.00',
    ref: { kind: 'order', order: {} as never, part: 'trigger' },
  })

  it('stands each label where its line is, and follows it when the scale moves', () => {
    const { owner, move } = fakeOwner([{ id: 'position:MIS', y: 200 }])
    render(
      <LineLabels
        owner={owner}
        lines={[line()]}
        more={null}
        bookAt={null}
        prices={prices}
        quiet={false}
      />
    )
    const target = () => screen.getByTestId('chart-lines').firstElementChild as HTMLElement
    expect(target().style.top).toBe('176px')
    expect(target().style.height).toBe('48px')
    act(() => move([{ id: 'position:MIS', y: 260 }]))
    expect(target().style.top).toBe('236px')
    act(() => move([]))
    expect(screen.queryByText('Buy 65 @ 131.20')).toBeNull()
  })

  it('is read, not pressed, without an action; with one, a label is a button that names its line', () => {
    const { owner } = fakeOwner([
      { id: 'position:MIS', y: 100 },
      { id: 'order:A1:trigger', y: 300 },
    ])
    const onOpen = vi.fn()
    const { rerender } = render(
      <LineLabels
        owner={owner}
        lines={[line(), order]}
        more={null}
        bookAt={null}
        prices={prices}
        quiet={false}
      />
    )
    expect(screen.queryByRole('button')).toBeNull()
    rerender(
      <LineLabels
        owner={owner}
        lines={[line(), order]}
        more={null}
        bookAt={null}
        prices={prices}
        quiet={false}
        onOpen={onOpen}
      />
    )
    const button = screen.getByRole('button', { name: /SELL 65 SL-M 120\.00.*Open its actions/ })
    button.click()
    expect(onOpen).toHaveBeenCalledWith(['order:A1:trigger'])
  })

  it('gives lines too close for a target each one target, which names how many', () => {
    const { owner } = fakeOwner([
      { id: 'position:MIS', y: 100 },
      { id: 'order:A1:trigger', y: 110 },
    ])
    const onOpen = vi.fn()
    render(
      <LineLabels
        owner={owner}
        lines={[line(), order]}
        more={null}
        bookAt={null}
        prices={prices}
        quiet={false}
        onOpen={onOpen}
      />
    )
    const button = screen.getByRole('button', { name: /2 lines here: choose one/ })
    expect(button.textContent).toContain('+1')
    button.click()
    expect(onOpen).toHaveBeenCalledWith(['position:MIS', 'order:A1:trigger'])
  })

  it('says a stale line is as of its last read, in grey with a warning mark, and what the cap left out', () => {
    const { owner } = fakeOwner([{ id: 'position:MIS', y: 100 }])
    render(
      <LineLabels
        owner={owner}
        lines={[line({ stale: true, note: 'as of 10:42:07' })]}
        more="2 more orders · see Orders"
        bookAt={BOOK_AT}
        prices={prices}
        quiet={false}
      />
    )
    const label = screen.getByText('Buy 65 @ 131.20').closest('[data-line]') as HTMLElement
    expect(label.dataset.stale).toBe('true')
    expect(label.textContent).toContain('as of 10:42:07')
    expect(label.querySelector('[data-stale]')).not.toBeNull()
    expect(screen.getByTestId('chart-lines-more').textContent).toBe('2 more orders · see Orders')
  })
})
