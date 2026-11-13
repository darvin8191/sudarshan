/**
 * F9, the page: the words of a row that is not followed, the screen-reader text, a price sort on a list
 * longer than the window, and the page with the switch off. Real `useLiveRows` over the market-data test
 * harness; the REST read is counted.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const read = vi.hoisted(() => ({ calls: [] as string[][], fail: false }))
/** The change of S<n> on the previous close of 100: a spread nothing in list order would produce. */
const priceOf = (name: string) => 100 + ((Number(name.slice(1)) * 37) % 101)
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn(
      async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => {
        read.calls.push(symbols.map((each) => `${each.exchange}:${each.symbol}`))
        if (read.fail) throw new Error('down')
        return {
          status: 'success',
          results: symbols.map((each) => ({
            symbol: each.symbol,
            exchange: each.exchange,
            data: {
              ltp: priceOf(each.symbol),
              prev_close: 100,
              open: 100,
              high: 0,
              low: 0,
              volume: 0,
              oi: 0,
              bid: 0,
              ask: 0,
            },
          })),
        }
      }
    ),
  },
}))

import type { WatchlistItem } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { FOLLOW_CAP, WINDOW } from './follow'
import { LiveList } from './LiveList'
import { rowView } from './rows'
import type { LiveRow } from './useLiveRows'
import type { Pref } from './v2/types'
import { WatchlistRows } from './WatchlistRows'

const T0 = Date.UTC(2026, 10, 13, 5, 12) // 10:42 IST

const itemsOf = (count: number): WatchlistItem[] =>
  Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    symbol: `S${index}`,
    exchange: 'NSE',
    position: index,
  }))

function outsideRow(reason: 'cap' | 'window', at: number | null): LiveRow {
  const quote = {
    ltp: 123.45,
    prev_close: 100,
    open: 100,
    high: 0,
    low: 0,
    volume: 0,
    oi: 0,
    bid: 0,
    ask: 0,
  }
  return {
    item: { id: 1, symbol: 'ZZZ', exchange: 'NSE', position: 0 },
    key: 'NSE:ZZZ',
    view: rowView(at === null ? undefined : { quote, at }, undefined),
    direction: null,
    stale: false,
    tickAt: null,
    outside: reason,
  }
}
const rowsProps = {
  loading: false,
  editing: false,
  onOpen: () => {},
  onMove: () => {},
  onRemove: () => {},
}

describe('a row that is not followed', () => {
  it('shows its last read, with the time, a clock and the cap in words', () => {
    render(<WatchlistRows {...rowsProps} rows={[outsideRow('cap', T0)]} />)
    const row = screen.getByRole('button', { name: /ZZZ/ })
    expect(row).toHaveAttribute('data-outside', 'cap')
    expect(
      within(row).getByText(/Price at 10:42 · not real-time: this list follows 250 prices at most/)
    ).toBeInTheDocument()
    expect(within(row).getByText('123.45')).toBeInTheDocument()
    expect(row.querySelector('svg')).not.toBeNull() // the clock beside the words: never colour alone
  })

  it('says "outside the 250 followed" to a screen reader, and "outside the 120 followed" for a window', () => {
    const { rerender } = render(<WatchlistRows {...rowsProps} rows={[outsideRow('cap', T0)]} />)
    expect(screen.getByText('outside the 250 followed')).toHaveClass('sr-only')
    rerender(<WatchlistRows {...rowsProps} rows={[outsideRow('window', T0)]} />)
    expect(screen.getByText('outside the 120 followed')).toHaveClass('sr-only')
    expect(
      screen.getByText(/not real-time: this list follows 120 rows at a time/)
    ).toBeInTheDocument()
  })

  it('says so when no read has landed, and a followed row says nothing of the kind', () => {
    const followed: LiveRow = { ...outsideRow('cap', T0), outside: undefined }
    render(<WatchlistRows {...rowsProps} rows={[outsideRow('cap', null), followed]} />)
    expect(screen.getByText(/No price read yet · not real-time/)).toBeInTheDocument()
    expect(screen.getAllByText(/not real-time/)).toHaveLength(1)
    expect(screen.getAllByRole('button')).toHaveLength(2)
  })
})

const pref = (over: Partial<Pref> = {}): Pref => ({
  sort: 'list',
  descending: false,
  basis: 'close',
  held: false,
  ...over,
})

function Page({ items, v2, p }: { items: WatchlistItem[]; v2: boolean; p: Pref }) {
  return (
    <LiveList
      items={items}
      apiKey="k"
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Big"
      openItemId={null}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => {}}
      onOrder={() => {}}
      onAlert={() => {}}
      flagNames={v2 ? [] : undefined}
      viewPref={
        v2 ? { ref: 'builtin:big', pref: p, save: async () => {}, problem: null } : undefined
      }
    />
  )
}

function mount(props: { items: WatchlistItem[]; v2: boolean; p?: Pref }) {
  const client = createQueryClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page items={props.items} v2={props.v2} p={props.p ?? pref()} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const names = () =>
  [...document.querySelectorAll('li > button')].map(
    (button) => /^S\d+/.exec(button.textContent ?? '')?.[0] ?? ''
  )
const viewLine = () => document.querySelector('[data-view-line]')?.textContent ?? ''
const live = () => feed.live().map((key) => key.replace(/:LTP$/, ''))

beforeEach(() => {
  feed.reset()
  frames.install()
  fakeClock()
  vi.setSystemTime(T0)
  read.calls = []
  read.fail = false
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const settle = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
    frames.flush()
  })
}

describe('a price sort on a list longer than the window', { timeout: 60_000 }, () => {
  const items = itemsOf(400)
  const ranked = [...items.keys()]
    .map((index) => ({ name: `S${index}`, change: priceOf(`S${index}`) - 100, index }))
    .sort((a, b) => b.change - a.change || a.index - b.index)

  it('reads one snapshot of every row first, ranks all of them on it and says so', async () => {
    mount({ items, v2: true, p: pref({ sort: 'change', descending: true }) })
    await settle()
    await settle()
    // The open read, and the sort's own read of every row: 400 keys each, one call each.
    expect(read.calls.map((call) => call.length)).toEqual([400, 400])
    // Rows ranked from outside the window (S350 and beyond) are on top: it was ranked on the snapshot.
    const top = names().slice(0, 5)
    expect(top).toEqual(ranked.slice(0, 5).map((each) => each.name))
    expect(ranked.slice(0, 5).some((each) => each.index >= WINDOW)).toBe(true)
    expect(viewLine()).toMatch(
      /^Sorted by % change, high first, at \d\d:\d\d, from prices read at \d\d:\d\d·Sort again$/
    )
    // The socket follows the first 120 rows as they are now shown.
    expect(live()).toHaveLength(WINDOW)
    expect(live()).toContain(`NSE:${ranked[0].name}`)
  })

  it('reads again on Sort again, and a failed read takes no snapshot', async () => {
    mount({ items, v2: true, p: pref({ sort: 'change', descending: true }) })
    await settle()
    await settle()
    expect(read.calls).toHaveLength(2)
    act(() => (document.querySelector('[data-view-line] button') as HTMLElement).click())
    await settle()
    expect(read.calls).toHaveLength(3)
    expect(read.calls[2]).toHaveLength(400)
  })

  it('takes no snapshot when the read fails, and ranks on the next one', async () => {
    read.fail = true
    mount({ items, v2: true, p: pref({ sort: 'change', descending: true }) })
    await settle()
    await settle()
    expect(names().slice(0, 3)).toEqual(['S0', 'S1', 'S2']) // still the list's own order
    expect(viewLine()).not.toMatch(/at \d\d:\d\d/)
    read.fail = false
    act(() => (document.querySelector('[data-view-line] button') as HTMLElement).click())
    await settle()
    await settle()
    expect(names()[0]).toBe(ranked[0].name)
    expect(viewLine()).toMatch(/from prices read at \d\d:\d\d/)
  })

  it('needs no read for a name sort, which ranks on no price', async () => {
    mount({ items, v2: true, p: pref({ sort: 'name', descending: false }) })
    await settle()
    expect(read.calls).toHaveLength(1)
    expect(names().slice(0, 3)).toEqual(['S0', 'S1', 'S2'])
  })

  it('leaves a list that fits to rank on what it shows, with no read of its own', async () => {
    mount({ items: itemsOf(FOLLOW_CAP), v2: true, p: pref({ sort: 'change', descending: true }) })
    await settle()
    await settle()
    expect(read.calls).toHaveLength(1)
    expect(viewLine()).not.toMatch(/from prices read at/)
  })
})

describe('with the switch off', () => {
  it("renders today's page: every row followed, no words of a followed row, no window", async () => {
    mount({ items: itemsOf(20), v2: false })
    await settle()
    expect(live()).toHaveLength(20)
    expect(screen.queryByText(/not real-time/)).toBeNull()
    expect(document.querySelector('[data-outside]')).toBeNull()
    expect(document.querySelector('[data-row-index]')).toBeNull()
    expect(read.calls).toHaveLength(1)
  })

  it('puts nothing else of v2 on a long list either: the window needs the switch', async () => {
    mount({ items: itemsOf(300), v2: false })
    await settle()
    expect(live()).toHaveLength(300)
  })
})
