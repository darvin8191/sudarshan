/**
 * M8.3 GA6.2, the page: the figures beside each price, tick by tick, as many as the list's width
 * holds, and the mode they stream in. Real `LiveList` and `useLiveRows` over the market-data test
 * harness; the REST snapshot and the follow rows' `resolve` are stand-ins.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})
const quotes = vi.hoisted(() => ({ open: 1495, reads: [] as string[][] }))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: vi.fn(async (_key: string, symbols: { symbol: string; exchange: string }[]) => {
      quotes.reads.push(symbols.map((each) => `${each.exchange}:${each.symbol}`))
      return {
        status: 'success',
        results: symbols.map((each) => ({
          symbol: each.symbol,
          exchange: each.exchange,
          data: {
            ltp: 1500,
            prev_close: 1490,
            open: quotes.open,
            high: 1510,
            low: 1480,
            volume: 120000,
            oi: 0,
            bid: 0,
            ask: 0,
          },
        })),
      }
    }),
  },
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: vi.fn(async () => ({ status: 'success', underlyings: [] })) },
}))

import type { WatchlistItem } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { drawnColumns } from './FigureCells'
import { answer, pairAnswer, pairRule, rule } from './followFixtures'
import { LiveList } from './LiveList'
import { rowView } from './rows'
import type { LiveRow } from './useLiveRows'
import type { Figure, FollowRow, Pref, Resolved } from './v2/types'
import { WatchlistRows } from './WatchlistRows'

const T0 = Date.UTC(2026, 10, 13, 4, 42) // 10:12 IST
const b = () => bridge.current as NonNullable<typeof bridge.current>
const answers = new Map<number, () => Resolved>()

const items = (count = 2): WatchlistItem[] =>
  ['INFY', 'TCS', ...Array.from({ length: Math.max(0, count - 2) }, (_, i) => `S${i}`)]
    .slice(0, count)
    .map((symbol, index) => ({ id: index + 1, symbol, exchange: 'NSE', position: index }))
const pref = (cols: Figure[]): Pref => ({
  sort: 'list',
  descending: false,
  basis: 'close',
  held: false,
  cols,
})

interface Props {
  cols: Figure[]
  list?: WatchlistItem[]
  editing?: boolean
  readOnly?: boolean
  rules?: FollowRow[]
  /** The item whose sheet is open. */
  open?: number | null
}
function Page({
  cols,
  list = items(),
  editing = false,
  readOnly = false,
  rules,
  open = null,
}: Props) {
  return (
    <LiveList
      items={list}
      apiKey="k"
      isOpen={() => true}
      loading={false}
      editing={editing}
      readOnly={readOnly}
      listName="Swing"
      openItemId={open}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => {}}
      onOrder={() => {}}
      onAlert={() => {}}
      flagNames={[]}
      viewPref={{ ref: 'list:1', pref: pref(cols), save: async () => {}, problem: null }}
      followRows={rules && { rules, openRowId: null, onOpenRow: () => {}, onForm: () => {} }}
    />
  )
}
function mount(props: Props) {
  const client = createQueryClient()
  const tree = (p: Props) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page {...p} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const out = render(tree(props))
  return { ...out, again: (p: Props) => out.rerender(tree(p)) }
}
const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
    frames.flush()
  })
}
const row = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) })
const cells = (scope: HTMLElement) =>
  [...scope.querySelectorAll('[data-figure]')].map((cell) => cell.getAttribute('data-figure'))
const modes = () => new Set(feed.live().map((key) => key.split(':').at(-1)))

beforeEach(() => {
  feed.reset()
  frames.install()
  fakeClock()
  vi.setSystemTime(T0)
  b().reset()
  answers.clear()
  quotes.open = 1495
  quotes.reads = []
  b().on('resolve', (call) => ({
    rows: (call.body as { rows: Array<{ id: number }> }).rows.map((each) =>
      (answers.get(each.id) ?? (() => answer(25900, { id: each.id })))()
    ),
  }))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the mode a list streams in (T1)', () => {
  it('stays in LTP mode with no figures, or with only those an LTP frame and the snapshot give', async () => {
    const page = mount({ cols: [] })
    await settle()
    expect(feed.live()).toEqual(['NSE:INFY:LTP', 'NSE:TCS:LTP'])
    page.again({ cols: ['open', 'prev'] })
    await settle()
    expect(modes()).toEqual(new Set(['LTP']))
  })

  it('streams every row in Quote mode while a figure that needs it is shown, and goes back', async () => {
    const page = mount({ cols: ['high', 'low'] })
    await settle()
    expect(feed.live().sort()).toEqual(['NSE:INFY:Quote', 'NSE:TCS:Quote'])
    page.again({ cols: ['prev', 'high'] })
    await settle()
    expect(modes()).toEqual(new Set(['Quote']))
    page.again({ cols: ['prev', 'open', 'high'] }) // upright: the third is not shown, so costs nothing
    await settle()
    expect(modes()).toEqual(new Set(['LTP']))
    expect(feed.live()).toHaveLength(2) // each move released the other mode's subscription
    page.unmount()
    expect(feed.live()).toEqual([]) // and leaving releases them all
  })
})

describe('the figures in the rows (M8.3)', () => {
  it('shows two with the phone upright and up to six on its side', async () => {
    const six: Figure[] = ['high', 'low', 'open', 'prev', 'volume', 'oi']
    mount({ cols: six })
    await settle()
    expect(cells(row('INFY'))).toEqual(['high', 'low'])
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 768,
    } as DOMRect)
    mount({ cols: six })
    await settle()
    expect(cells(screen.getAllByRole('button', { name: /^INFY/ }).at(-1) as HTMLElement)).toEqual(
      six
    )
  })

  // v0.17.0 review M1: at a 7rem floor "BANKNIFTY 58500 PE" was cut to "BANKNIFTY 5850..." beside
  // the figures. jsdom lays nothing out, so what keeps the name whole is pinned here; the phone spec
  // (e2e/sd-watchlist-figures) measures it upright and on its side.
  it('keeps the name its whole width and gives the figures only what is left', async () => {
    mount({ cols: ['high', 'low'] })
    await settle()
    const name = row('INFY').querySelector('span[title="INFY"]')?.parentElement?.parentElement
    expect(name?.className.split(/\s+/)).toEqual(
      expect.arrayContaining(['flex-initial', 'min-w-0'])
    )
    expect(name?.className).not.toMatch(/min-w-\[|flex-1/)
    expect(row('INFY').querySelector('[data-figures]')?.className.split(/\s+/)).toEqual(
      expect.arrayContaining(['flex-1', 'min-w-0', 'flex-wrap', 'overflow-hidden'])
    )
    // Without figures the name takes the room, as before.
    mount({ cols: [] })
    await settle()
    const bare = screen.getAllByRole('button', { name: /^INFY/ }).at(-1) as HTMLElement
    expect(
      bare.querySelector('span[title="INFY"]')?.parentElement?.parentElement?.className
    ).toMatch(/\bflex-1\b/)
  })

  it('moves tick by tick and says each figure to a screen reader', async () => {
    mount({ cols: ['high', 'volume'] })
    await settle()
    const infy = row('INFY')
    expect(within(infy).getByText('Day high 1,510.00')).toHaveClass('sr-only')
    expect(within(infy).getByText('Volume 1.2L')).toBeInTheDocument()
    act(() => {
      feed.push('INFY', 'NSE', { ltp: 1530, high: 1530, volume: 250000 })
      frames.flush()
    })
    expect(within(infy).getByText('1,530.00', { selector: '[data-figure] span' })).toBeVisible()
    expect(within(infy).getByText('Volume 2.5L')).toBeInTheDocument()
    expect(within(infy).getByText('Vol')).toHaveClass('text-[color:var(--text-tertiary)]')
  })

  it('turns grey with the row when the feed is down and its figures are old', async () => {
    mount({ cols: ['high', 'low'] })
    await settle()
    act(() => {
      feed.push('INFY', 'NSE', { ltp: 1501, high: 1512 })
      frames.flush()
    })
    const value = () => within(row('INFY')).getByText('1,512.00')
    expect(value()).toHaveClass('text-[color:var(--text-primary)]')
    act(() => feed.setState({ isAuthenticated: false }))
    await settle(11_000)
    expect(row('INFY').closest('li')).toHaveAttribute('data-stale', 'true')
    expect(value()).toHaveClass('text-[color:var(--stale)]')
  })

  it('is grey on a row past the cap, beside its existing words', () => {
    const snap = {
      quote: {
        ltp: 1500,
        prev_close: 1490,
        open: 1495,
        high: 1510,
        low: 1480,
        volume: 0,
        oi: 0,
        bid: 0,
        ask: 0,
      },
      at: T0,
    }
    const outside: LiveRow = {
      item: { id: 9, symbol: 'ZZZ', exchange: 'NSE', position: 0 },
      key: 'NSE:ZZZ',
      view: rowView(snap, undefined),
      direction: null,
      stale: false,
      tickAt: null,
      outside: 'cap',
      snap,
    }
    render(
      <WatchlistRows
        rows={[outside]}
        loading={false}
        editing={false}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        figures={['high', 'low']}
      />
    )
    const zzz = row('ZZZ')
    expect(zzz).toHaveAttribute('data-outside', 'cap')
    expect(
      within(zzz).getByText(/not real-time: this list follows 250 prices at most/)
    ).toBeVisible()
    expect(within(zzz).getByText('1,510.00')).toHaveClass('text-[color:var(--stale)]')
    expect(within(zzz).getByText('H')).toHaveClass('text-[color:var(--stale)]')
  })

  it('reads a row again at its first tick when today’s open shown is not known yet', async () => {
    quotes.open = 0 // read before the opening
    mount({ cols: ['open', 'prev'] })
    await settle()
    expect(quotes.reads).toHaveLength(1)
    expect(within(row('INFY')).getByText('Today’s open not received yet')).toBeInTheDocument()
    quotes.open = 1496
    await settle(1_000) // the first tick comes a moment after the read
    act(() => {
      feed.push('INFY', 'NSE', { ltp: 1501 })
      frames.flush()
    })
    await settle()
    expect(quotes.reads.at(-1)).toEqual(['NSE:INFY'])
    expect(within(row('INFY')).getByText('Today’s open 1,496.00')).toBeInTheDocument()
  })

  it('streams for no figure that no row has room for, and counts what the rows draw', async () => {
    // Laid out as on a phone upright with Larger text: the first column wraps below the first line.
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (
      this: HTMLElement
    ) {
      return this.hasAttribute('data-figures') ? 32 : 0
    })
    const wrapped = vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockReturnValue(32)
    const page = mount({ cols: ['high', 'low'] })
    await settle()
    await settle()
    expect(modes()).toEqual(new Set(['LTP']))
    expect(drawnColumns(document.querySelector('section'))).toEqual({ least: 0, most: 0 })
    // Room again (the phone turned): the rows draw the column and stream for it.
    wrapped.mockReturnValue(0)
    page.again({ cols: ['low', 'high'] })
    await settle()
    await settle()
    expect(drawnColumns(document.querySelector('section'))).toEqual({ least: 1, most: 1 })
    expect(modes()).toEqual(new Set(['Quote']))
  })

  it('draws none while editing, and none in a family render', async () => {
    const page = mount({ cols: ['high', 'low'], editing: true })
    await settle()
    expect(document.querySelector('[data-figures]')).toBeNull()
    page.again({ cols: ['high', 'low'], readOnly: true })
    await settle()
    expect(document.querySelector('[data-figures]')).toBeNull()
    expect(modes()).toEqual(new Set(['LTP']))
  })

  it('shows them on a row that follows the price, never on a strike row', async () => {
    answers.set(1, () => answer(25900))
    answers.set(2, () => pairAnswer(25900, { id: 2 }))
    mount({ cols: ['high', 'oi'], rules: [rule(), pairRule({ id: 2 })] })
    await settle()
    const follow = document.querySelector('[data-follow-row="1"]') as HTMLElement
    expect(cells(follow)).toEqual(['high', 'oi'])
    const strike = document.querySelector('[data-strike-row="2"]') as HTMLElement
    expect(strike.querySelector('[data-figures]')).toBeNull()
    expect(feed.live()).toContain('NFO:NIFTY24NOV2625900CE:Quote')
  })
})

describe('an expired contract in a v2 list (M8.2 TD1)', () => {
  const EXPIRED = { id: 9, symbol: 'NIFTY03NOV2622700PE', exchange: 'NFO', position: 1 }

  it('says it once, with its day: the expired mark is not drawn beside the name again', () => {
    const gone: LiveRow = {
      item: EXPIRED,
      key: 'NFO:NIFTY03NOV2622700PE',
      view: rowView(undefined, undefined),
      direction: null,
      stale: false,
      tickAt: null,
      expired: '2026-11-03',
    }
    const marks = new Map([[gone.key, { flag: null, note: false, expired: true }]])
    render(
      <WatchlistRows
        rows={[gone]}
        loading={false}
        editing={false}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        marks={marks}
        figures={['high']}
      />
    )
    const button = row('NIFTY 22700 PE')
    expect(button.querySelector('[data-expired-on="2026-11-03"]')).toHaveTextContent(
      'Expired on 3 Nov 2026'
    )
    expect(button.querySelector('[data-expired]')).toBeNull()
    expect(button.textContent?.match(/Expired/g)).toHaveLength(1)
  })

  it('draws no figure and no price, and is neither read nor followed, its sheet open or not', async () => {
    vi.setSystemTime(Date.parse('2026-11-23T11:20:00+05:30'))
    b().on('symbol', () => ({
      key: 'NFO:NIFTY03NOV2622700PE',
      flag: null,
      flag_name: null,
      note: null,
      note_at: null,
      expired: true,
      version: 1,
      lists: [],
    }))
    const page = mount({ cols: ['high', 'volume'], list: [...items(1), EXPIRED] })
    await settle()
    const gone = row('NIFTY 22700 PE')
    expect(within(gone).getByText('Expired on 3 Nov 2026')).toBeInTheDocument()
    expect(cells(gone)).toEqual([])
    expect(gone.closest('li')?.textContent).not.toMatch(/not real-time|No price/)
    expect(cells(row('INFY'))).toEqual(['high', 'volume'])
    page.again({ cols: ['high', 'volume'], list: [...items(1), EXPIRED], open: EXPIRED.id })
    await settle(60_000)
    expect(screen.getByRole('dialog', { name: 'NIFTY 22700 PE' })).toHaveTextContent(
      'It is no longer quoted or traded. Remove it from Swing?'
    )
    expect(feed.live().some((key) => key.includes(EXPIRED.symbol))).toBe(false)
    expect(quotes.reads.flat()).not.toContain(`NFO:${EXPIRED.symbol}`)
    expect(quotes.reads.flat()).toContain('NSE:INFY')
  })
})
