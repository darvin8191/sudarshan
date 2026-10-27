import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

import { feed } from '@/test/marketDataHarness'
import type { Position } from '@/types/trading'
import { STALE_MS } from './live'
import { type DashboardFunds, marginUse, PnlCard } from './PnlCard'

const MINUS = '−'

function funds(overrides: Partial<DashboardFunds> = {}): DashboardFunds {
  return {
    availablecash: '48320.50',
    collateral: '0.00',
    m2munrealized: '650.75',
    m2mrealized: '1800.00',
    utiliseddebits: '12400.00',
    ...overrides,
  }
}

describe('PnlCard', () => {
  it("adds realised and unrealised into today's P&L, signed, with an up arrow", () => {
    render(<PnlCard funds={funds()} loading={false} />)

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent('+₹2,450.75')
    expect(total).toHaveClass('text-[color:var(--up)]')
    expect(total.querySelector('svg')).toHaveAttribute('data-direction', 'up')
    expect(screen.getByText('+₹1,800.00')).toBeInTheDocument()
    expect(screen.getByText('+₹650.75')).toBeInTheDocument()
  })

  it('shows a loss with a true minus and a down arrow, never colour alone', () => {
    render(
      <PnlCard
        funds={funds({ m2mrealized: '-840.00', m2munrealized: '-2310.25' })}
        loading={false}
      />
    )

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent(`${MINUS}₹3,150.25`)
    expect(total).toHaveClass('text-[color:var(--down)]')
    expect(total.querySelector('svg')).toHaveAttribute('data-direction', 'down')
  })

  it('gives a flat day no sign and no arrow', () => {
    render(<PnlCard funds={funds({ m2mrealized: '0', m2munrealized: '0.00' })} loading={false} />)

    const total = screen.getByTestId('pnl-total')
    expect(total).toHaveTextContent('₹0.00')
    expect(total).toHaveClass('text-foreground')
    expect(total.querySelector('svg')).toBeNull()
  })

  it('shows the funds available and the margin in use against all the margin there is', () => {
    render(<PnlCard funds={funds()} loading={false} />)

    expect(screen.getByText('₹48,320.50')).toBeInTheDocument()
    expect(screen.getByText('Margin used ₹12,400 of ₹60,721')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
    const fill = screen.getByTestId('margin-bar').firstElementChild as HTMLElement
    expect(Number.parseFloat(fill.style.width)).toBeCloseTo((12400 / 60720.5) * 100, 4)
  })

  it('counts pledged collateral into the margin and lists it when there is some', () => {
    render(<PnlCard funds={funds({ collateral: '39279.50' })} loading={false} />)

    expect(screen.getByText('Collateral')).toBeInTheDocument()
    expect(screen.getByText('₹39,279.50')).toBeInTheDocument()
    expect(screen.getByText('Margin used ₹12,400 of ₹1,00,000')).toBeInTheDocument()
    expect(screen.getByText('12%')).toBeInTheDocument()
  })

  it('says there is no margin in use when the broker reports nothing at all', () => {
    render(
      <PnlCard
        funds={funds({
          availablecash: '0.00',
          utiliseddebits: '0.00',
          m2mrealized: '0',
          m2munrealized: '0',
        })}
        loading={false}
      />
    )

    expect(screen.getByText('No margin in use')).toBeInTheDocument()
    expect(screen.queryByTestId('margin-bar')).toBeNull()
  })

  it('shows placeholders, not zeros, while the funds load', () => {
    const { container } = render(<PnlCard funds={null} loading />)

    expect(screen.queryByTestId('pnl-total')).toBeNull()
    expect(screen.queryByText('₹0.00')).toBeNull()
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0)
    expect(container.firstElementChild).toHaveAttribute('aria-busy', 'true')
  })
})

describe('PnlCard, live', () => {
  const CALL = 'NIFTY28OCT2525900CE'
  const OPEN = () => true

  function position(overrides: Partial<Position> = {}): Position {
    return {
      symbol: CALL,
      exchange: 'NFO',
      product: 'NRML',
      quantity: 65,
      average_price: 120,
      ltp: 130,
      pnl: 650,
      pnlpercent: 8.33,
      ...overrides,
    }
  }

  /** The book as read one second ago, with the broker's funds read just after it. */
  function card(
    positions: Position[],
    overrides: Partial<DashboardFunds> = {},
    extra: { marketOpen?: () => boolean | null; syncFailed?: boolean } = {}
  ) {
    const at = Date.now() - 1000
    return (
      <PnlCard
        funds={funds({ m2mrealized: '1800.00', m2munrealized: '650.00', ...overrides })}
        loading={false}
        positions={{ positions, at }}
        syncedAt={at + 300}
        marketOpen={extra.marketOpen ?? OPEN}
        syncFailed={extra.syncFailed}
      />
    )
  }

  const tick = (ltp: number, source: 'websocket' | 'rest' = 'websocket', symbol = CALL) =>
    act(() => feed.push(symbol, 'NFO', { ltp }, source))
  const total = () => screen.getByTestId('pnl-total')
  const unrealised = () => screen.getByTestId('pnl-unrealised')
  const line = (container: HTMLElement) => container.querySelector('[data-feed]')

  beforeEach(() => {
    feed.reset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("moves the broker's unrealised P&L with every tick of an open long", () => {
    const { container } = render(card([position()]))
    expect(feed.live()).toEqual([`NFO:${CALL}:LTP`])
    expect(total()).toHaveTextContent('+₹2,450.00')
    expect(screen.getByText('1 open position')).toBeInTheDocument()

    tick(134)
    expect(unrealised()).toHaveTextContent('+₹910.00')
    expect(total()).toHaveTextContent('+₹2,710.00')
    expect(line(container)).toHaveTextContent('Real-time')

    tick(112)
    expect(unrealised()).toHaveTextContent(`${MINUS}₹520.00`)
    expect(total()).toHaveTextContent('+₹1,280.00')
    expect(total().querySelector('svg')).toHaveAttribute('data-direction', 'up')
  })

  it('moves a short the other way', () => {
    render(card([position({ quantity: -65 })], { m2munrealized: '-650.00' }))
    tick(134)
    expect(unrealised()).toHaveTextContent(`${MINUS}₹910.00`)
  })

  it('moves two NIFTY lots by 130 a rupee, as the broker counts F&O quantity in units', () => {
    render(card([position({ quantity: 130 })], { m2munrealized: '1300.00' }))
    tick(131)
    expect(unrealised()).toHaveTextContent('+₹1,430.00')
  })

  it('after a partial exit moves only what is still held, and leaves realised as reported', () => {
    render(card([position({ quantity: 65, today_realized_pnl: 900 })], { m2mrealized: '900.00' }))
    tick(132)
    expect(screen.getByText('+₹900.00')).toBeInTheDocument()
    expect(unrealised()).toHaveTextContent('+₹780.00')
  })

  it("shows the broker's figures when flat, subscribes to nothing and never says Live", () => {
    const { container } = render(
      card([position({ quantity: 0, average_price: 0 })], { m2munrealized: '0.00' })
    )
    expect(feed.opened).toEqual([])
    expect(total()).toHaveTextContent('+₹1,800.00')
    expect(screen.getByText('No open positions')).toBeInTheDocument()
    expect(line(container)).toHaveTextContent(/^Updated \ds ago$/)
  })

  it('waits for no prices when flat and the read time is not known: no line at all', () => {
    const { container } = render(
      <PnlCard
        funds={funds({ m2mrealized: '1800.00', m2munrealized: '0.00' })}
        loading={false}
        positions={{ positions: [position({ quantity: 0, average_price: 0 })], at: Date.now() }}
        syncedAt={null}
        marketOpen={OPEN}
      />
    )
    expect(screen.getByText('No open positions')).toBeInTheDocument()
    expect(line(container)).toBeNull()
    expect(screen.queryByText('Waiting for prices')).toBeNull()
  })

  it('ignores a tick older than the book, which already counts it', () => {
    render(card([position()]))
    act(() => feed.push(CALL, 'NFO', { ltp: 200 }, 'websocket', Date.now() - 5000))
    expect(unrealised()).toHaveTextContent('+₹650.00')
  })

  it('moves on a REST fallback price, but the line never says Live for one', () => {
    const { container } = render(card([position()]))
    tick(131, 'rest')
    expect(unrealised()).toHaveTextContent('+₹715.00')
    expect(line(container)).toHaveAttribute('data-feed', 'delayed')
  })

  it('says stale when the market is open and no tick has come for ten seconds', async () => {
    vi.useFakeTimers()
    const { container } = render(card([position()]))
    tick(131)
    expect(line(container)).toHaveTextContent('Real-time')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STALE_MS + 1000)
    })
    expect(line(container)).toHaveAttribute('data-feed', 'stale')
    expect(line(container)).toHaveTextContent('Stale · updated 11s ago')
    // Greyed, but still signed and still pointing the right way.
    expect(total()).toHaveClass('text-muted-foreground')
    expect(total()).toHaveTextContent('+₹2,515.00')
    expect(total().querySelector('svg')).toHaveAttribute('data-direction', 'up')
    expect(unrealised()).toHaveClass('text-muted-foreground')

    tick(132)
    expect(total()).toHaveClass('text-[color:var(--up)]')
  })

  it('after the phone wakes, says it is reconnecting over greyed figures until the first tick', async () => {
    vi.useFakeTimers()
    const { container } = render(card([position()]))
    tick(131)
    // Hidden: the shared socket is paused, and a minute goes by.
    act(() => feed.setState({ connectionState: 'paused', isConnected: false, isPaused: true }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    // Back in view, signing in again.
    act(() =>
      feed.setState({
        connectionState: 'authenticating',
        isConnected: true,
        isAuthenticated: false,
        isPaused: false,
      })
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(line(container)).toHaveAttribute('data-feed', 'connecting')
    expect(line(container)).toHaveTextContent('Reconnecting · updated 1m ago')
    expect(total()).toHaveClass('text-muted-foreground')

    act(() => feed.setState({ connectionState: 'authenticated', isAuthenticated: true }))
    tick(132)
    expect(line(container)).toHaveTextContent('Real-time')
    expect(total()).toHaveClass('text-[color:var(--up)]')
  })

  it('says the market is closed when it is', () => {
    const { container } = render(card([position()], {}, { marketOpen: () => false }))
    expect(line(container)).toHaveTextContent('Market closed')
  })

  it('starts again from each new read, so a move is never counted twice', () => {
    const { rerender } = render(card([position()]))
    tick(134)
    expect(unrealised()).toHaveTextContent('+₹910.00')

    // The re-read: the broker now reports the move itself, at its own price.
    rerender(card([position({ ltp: 134 })], { m2munrealized: '912.00' }))
    expect(unrealised()).toHaveTextContent('+₹912.00')
    tick(135)
    expect(unrealised()).toHaveTextContent('+₹977.00')
  })

  it('keeps the figures and says so when the last refresh failed', () => {
    render(card([position()], {}, { syncFailed: true }))
    expect(total()).toHaveTextContent('+₹2,450.00')
    expect(screen.getByRole('status')).toHaveTextContent(
      'Could not refresh just now. Showing the last figures.'
    )
  })

  it('closes its subscriptions when it goes away', () => {
    const { unmount } = render(card([position()]))
    unmount()
    expect(feed.live()).toEqual([])
    expect(feed.closed).toEqual([`NFO:${CALL}:LTP`])
  })
})

describe('marginUse', () => {
  it('reads a shortfall as all of the margin in use', () => {
    expect(marginUse(funds({ availablecash: '-2000', utiliseddebits: '15000' }))?.percent).toBe(100)
  })

  it('has nothing to measure when every figure is zero', () => {
    expect(marginUse(funds({ availablecash: '0', utiliseddebits: '0' }))).toBeNull()
  })

  it('treats junk from the API as zero rather than as a number', () => {
    expect(marginUse(funds({ availablecash: 'n/a', utiliseddebits: '100' }))?.percent).toBe(100)
  })
})
