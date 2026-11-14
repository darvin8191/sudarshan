/**
 * The Trades part in jsdom: the bridge is a stand-in answering as the contract says. Pinned: open
 * trades first with their P&L moving with ticks (sign, arrow and word), the live line under Open
 * and none under Closed, closed trades by day newest first, the empty sentences, the mode on every
 * read, and the re-read after an order update.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const api = vi.hoisted(() => ({ data: vi.fn() }))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  journalData: api.data,
}))
const socket = vi.hoisted(() => {
  const handlers = new Map<string, Set<(...args: unknown[]) => void>>()
  return {
    connected: true,
    on(event: string, handler: (...args: unknown[]) => void) {
      handlers.set(event, (handlers.get(event) ?? new Set()).add(handler))
      return this
    },
    off(event: string, handler: (...args: unknown[]) => void) {
      handlers.get(event)?.delete(handler)
      return this
    },
    fire(event: string, ...args: unknown[]) {
      for (const handler of [...(handlers.get(event) ?? [])]) handler(...args)
    },
  }
})
vi.mock('@/components/socket/SocketProvider', () => ({
  useSocketContext: () => ({ playAlertSound: () => {}, socket }),
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))
const access = vi.hoisted(() => ({ state: 'ready' as string }))
vi.mock('@/sudarshan/access', () => ({ usePriceAccess: () => ({ state: access.state }) }))

import { renderBook } from '@/test/bookPages'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { closedTrip, trip } from './fixtures'
import { EMPTY_WORDS, NO_TOKEN_WORDS, TripsList } from './TripsList'
import type { Tag, Trip } from './types'

const TAGS: Tag[] = [
  { id: 1, kind: 'mistake', name: 'Chased entry', colour: 'gold', archived: false, version: 1 },
]

function serve(open: Trip[], closed: Array<Trip & { mistakes?: number[] }>) {
  api.data.mockImplementation(async (op: string, args?: { status?: string }) => {
    if (op === 'tags_list') return TAGS
    if (op === 'trips') return args?.status === 'open' ? open : closed
    throw new Error(`unexpected op ${op}`)
  })
}

vi.setConfig({ testTimeout: 15_000 })

const flush = () => act(() => frames.flush())
const show = (mode: 'live' | 'sandbox' = 'live', onOpen = vi.fn()) =>
  renderBook('/journal', <TripsList mode={mode} onOpen={onOpen} />)

beforeEach(() => {
  fakeClock()
  frames.install()
  feed.reset()
  api.data.mockReset()
  access.state = 'ready'
})

describe('TripsList', () => {
  it('lists open trades first, each with its side, quantity, entry average, hold and strategy', async () => {
    serve(
      [
        trip({
          id: 1,
          symbol: 'SBIN',
          strategy: 'Flow: gap',
          opened_ms: Date.now() - 134 * 60_000,
        }),
        trip({ id: 2, symbol: 'INFY', side: 'short', entry_avg_u: 1_040_000, entry_qty: 40 }),
      ],
      [closedTrip({ id: 3, net_paise: 81250, close_day: 20261112 })]
    )
    show()
    const open = await screen.findAllByTestId('open-trip')
    expect(open).toHaveLength(2)
    expect(within(open[0]).getByText('LONG')).toBeTruthy()
    expect(within(open[0]).getByText('65 qty at 100.00')).toBeTruthy()
    expect(within(open[0]).getByText(/Held 2 h 14 min/)).toBeTruthy()
    expect(within(open[0]).getByText('Flow: gap')).toBeTruthy()
    expect(within(open[1]).getByText('SHORT')).toBeTruthy()
    expect(within(open[1]).getByText('40 qty at 104.00')).toBeTruthy()
    const order = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(order).toEqual(['Open', 'Closed'])
  })

  it('moves an open trade’s P&L with every tick, signed, with its arrow and its word', async () => {
    serve([trip({ id: 1 })], [])
    show()
    const card = await screen.findByTestId('open-trip')
    expect(within(card).getByText('Waiting for a price')).toBeTruthy()
    act(() => feed.push('SBIN', 'NSE', { ltp: 101.5 }))
    flush()
    expect(within(card).getByTestId('live-pnl').textContent).toContain('+₹97.50')
    expect(within(card).getByText('Profit')).toBeTruthy()
    act(() => feed.push('SBIN', 'NSE', { ltp: 99 }))
    flush()
    const pnl = within(card).getByTestId('live-pnl')
    expect(pnl.textContent).toContain('−₹65.00')
    expect(within(pnl).getByText('Loss')).toBeTruthy()
    expect(pnl.querySelector('[data-direction="down"]')).toBeTruthy()
  })

  it('says Real-time under Open while ticks arrive, and shows no live line under Closed', async () => {
    serve([trip({ id: 1 })], [closedTrip({ id: 3, net_paise: 500, close_day: 20261112 })])
    show()
    await screen.findByTestId('open-trip')
    act(() => feed.push('SBIN', 'NSE', { ltp: 101 }))
    flush()
    const openGroup = screen.getByRole('region', { name: 'Open trades' })
    expect(within(openGroup).getByText('Real-time')).toBeTruthy()
    const closedGroup = screen.getByRole('region', { name: 'Closed trades' })
    expect(within(closedGroup).queryByText(/Real-time|Reconnecting|Stale|Market closed/)).toBeNull()
  })

  it('shows a part-closed trade’s open quantity', async () => {
    serve([trip({ id: 1, entry_qty: 65, exit_qty: 25 })], [])
    show()
    const card = await screen.findByTestId('open-trip')
    expect(within(card).getByText('40 qty at 100.00')).toBeTruthy()
    expect(within(card).getByText('Part closed: 25 of 65 qty')).toBeTruthy()
  })

  it('groups closed trades by day, newest first, each with net, sign, hold, strategy and tags', async () => {
    serve(
      [],
      [
        closedTrip({
          id: 1,
          net_paise: -32500,
          close_day: 20261113,
          closed_ms: 300,
          hold_s: 14 * 60,
          strategy: 'ORB',
          mae_paise: -50000,
          mfe_paise: 10000,
          charges_actual_paise: 4049,
        }),
        closedTrip({ id: 2, net_paise: 0, close_day: 20261113, closed_ms: 200 }),
        Object.assign(closedTrip({ id: 3, net_paise: 81250, close_day: 20261112 }), {
          mistakes: [1],
        }),
      ]
    )
    show()
    const days = await screen.findAllByTestId('closed-day')
    expect(days).toHaveLength(2)
    expect(within(days[0]).getByText('Fri 13 Nov 2026')).toBeTruthy()
    expect(within(days[0]).getByText(/2 trades/)).toBeTruthy()
    expect(within(days[1]).getByText('Thu 12 Nov 2026')).toBeTruthy()
    const [loss, flat] = within(days[0]).getAllByTestId('closed-trip')
    expect(loss.textContent).toContain('−₹325.00')
    expect(within(loss).getByText('Loss')).toBeTruthy()
    expect(within(loss).getByText(/Held 14 min/)).toBeTruthy()
    expect(within(loss).getByText('ORB')).toBeTruthy()
    expect(within(loss).getByTestId('excursion-bar').getAttribute('aria-label')).toBe(
      'Worst −₹500.00, best +₹100.00'
    )
    expect(within(loss).queryByText('est.')).toBeNull()
    expect(within(flat).getByText('Flat')).toBeTruthy()
    expect(within(flat).getByText('est.')).toBeTruthy()
    const [win] = within(days[1]).getAllByTestId('closed-trip')
    expect(win.textContent).toContain('+₹812.50')
    expect(within(win).getByText('Chased entry')).toBeTruthy()
  })

  it('says it in words when there are no trades, and when there is no Upstox sign-in', async () => {
    serve([], [])
    const first = show()
    expect(await screen.findByText(EMPTY_WORDS)).toBeTruthy()
    first.unmount()
    access.state = 'upstox-logged-out'
    show()
    expect(await screen.findByText(NO_TOKEN_WORDS)).toBeTruthy()
  })

  it('asks for the mode on every read, and never mixes the two', async () => {
    serve([], [])
    show('sandbox')
    await screen.findByText(/No Sandbox trades yet/)
    const trips = api.data.mock.calls.filter(([op]) => op === 'trips')
    expect(trips.length).toBeGreaterThan(0)
    for (const [, args] of trips) expect(args.mode).toBe('sandbox')
  })

  it('says why it could not read, with the next step, and tries again', async () => {
    const user = userEvent.setup()
    api.data.mockRejectedValue(
      new Error(
        'The journal service is not answering. Your trades are safe; try again in a minute.'
      )
    )
    show()
    await screen.findByRole('button', { name: /Try again/ })
    serve([trip({ id: 1 })], [])
    await user.click(screen.getByRole('button', { name: /Try again/ }))
    expect(await screen.findByTestId('open-trip')).toBeTruthy()
  })

  it('keeps the last list and says so when a re-sync fails', async () => {
    serve([trip({ id: 1 })], [])
    show()
    await screen.findByTestId('open-trip')
    const reads = api.data.mock.calls.length
    api.data.mockRejectedValue(new Error('x'))
    act(() => socket.fire('order_update', {}))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    expect(api.data.mock.calls.length).toBeGreaterThan(reads)
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByTestId('open-trip')).toBeTruthy()
  })

  it('re-reads 3 s after an order update, once for a burst', async () => {
    serve([trip({ id: 1 })], [])
    show()
    await screen.findByTestId('open-trip')
    const reads = () =>
      api.data.mock.calls.filter(([op, a]) => op === 'trips' && a.status === 'open').length
    const before = reads()
    for (let n = 0; n < 5; n += 1) {
      act(() => socket.fire('order_update', {}))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })
    }
    expect(reads()).toBe(before)
    serve([trip({ id: 1 }), trip({ id: 9, symbol: 'INFY' })], [])
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    await waitFor(() => expect(screen.getAllByTestId('open-trip')).toHaveLength(2))
    expect(reads()).toBe(before + 1)
  })

  it('opens a trade’s sheet from its card, by its number', async () => {
    const user = userEvent.setup()
    const onOpen = vi.fn()
    serve([trip({ id: 4 })], [closedTrip({ id: 5, net_paise: 100, close_day: 20261112 })])
    show('live', onOpen)
    await user.click(await screen.findByTestId('open-trip'))
    await user.click(screen.getByTestId('closed-trip'))
    expect(onOpen.mock.calls).toEqual([[4], [5]])
  })

  it('offers earlier trades after a full page, and adds them without repeating', async () => {
    const page = Array.from({ length: 100 }, (_, n) =>
      closedTrip({ id: 1000 - n, net_paise: 100, close_day: 20261112, closed_ms: 5000 - n })
    )
    api.data.mockImplementation(async (op: string, args: { status?: string; before?: number }) => {
      if (op === 'tags_list') return TAGS
      if (args.status === 'open') return []
      if (args.before !== undefined)
        return [
          closedTrip({ id: 7, net_paise: -100, close_day: 20261110, closed_ms: 10 }),
          page[99],
        ]
      return page
    })
    show()
    await screen.findAllByTestId('closed-trip')
    fireEvent.click(screen.getByRole('button', { name: 'Show earlier trades' }))
    await screen.findByText('Tue 10 Nov 2026')
    expect(screen.getAllByTestId('closed-trip')).toHaveLength(101)
    // up to and including the last one's close: the service's page ends strictly before it
    expect(api.data.mock.calls.find(([, a]) => a?.before !== undefined)?.[1].before).toBe(
      5000 - 99 + 1
    )
    expect(screen.queryByRole('button', { name: 'Show earlier trades' })).toBeNull()
  })

  it('loses no trade that closed in the same second as the last one shown', async () => {
    // The service as it pages: closed trades newest first by close time, then id, and a later
    // page holds those that closed strictly before `before`.
    const all = [
      ...Array.from({ length: 99 }, (_, n) =>
        closedTrip({ id: 1000 - n, net_paise: 100, close_day: 20261112, closed_ms: 5000 - n })
      ),
      closedTrip({ id: 801, net_paise: 100, close_day: 20261111, closed_ms: 4000 }),
      closedTrip({ id: 800, net_paise: -50, close_day: 20261111, closed_ms: 4000 }),
      closedTrip({ id: 7, net_paise: -100, close_day: 20261110, closed_ms: 10 }),
    ]
    api.data.mockImplementation(
      async (op: string, args: { status?: string; before?: number; limit?: number }) => {
        if (op === 'tags_list') return TAGS
        if (args.status === 'open') return []
        const before = args.before
        const rows = before === undefined ? all : all.filter((t) => (t.closed_ms ?? 0) < before)
        return rows.slice(0, args.limit ?? 100)
      }
    )
    show()
    await screen.findAllByTestId('closed-trip')
    expect(screen.getAllByTestId('closed-trip')).toHaveLength(100) // 801 is the last of the page
    fireEvent.click(screen.getByRole('button', { name: 'Show earlier trades' }))
    await screen.findByText('Tue 10 Nov 2026')
    expect(screen.getAllByTestId('closed-trip')).toHaveLength(102) // 800 too, and once each
    expect(screen.queryByRole('button', { name: 'Show earlier trades' })).toBeNull()
  })
})
