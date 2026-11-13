/**
 * F7, the page: "Show what I hold" in the View sheet, the holdings line on the rows, the family
 * render, the books it reads and when, and the page with the switch off. The live rows are a
 * stand-in (`useLiveRows` mocked); the books are the trading API's two reads, mocked.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

const feed = vi.hoisted(() => ({ state: null as unknown }))
vi.mock('./useLiveRows', () => ({ useLiveRows: () => feed.state }))

const books = vi.hoisted(() => ({
  holdings: [] as unknown[],
  positions: [] as unknown[],
  getHoldings: vi.fn(),
  getPositions: vi.fn(),
}))
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getHoldings: (...args: unknown[]) => books.getHoldings(...args),
    getPositions: (...args: unknown[]) => books.getPositions(...args),
  },
}))

import { createQueryClient } from '@/app/providers'
import { LiveList } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import { rowView } from './rows'
import { useHeld } from './useHeld'
import type { LiveRow } from './useLiveRows'
import { useViewPref } from './ViewSheet'
import type { Pref, View } from './v2/types'
import { useListView } from './v2/useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const T = 1_760_000_000_000
const ISIN = 'INE002A01018'

const ROWS = [
  { symbol: 'RELIANCE', exchange: 'NSE', isin: ISIN },
  { symbol: 'TCS', exchange: 'NSE', isin: 'INE467B01029' },
  { symbol: 'NIFTY25NOVFUT', exchange: 'NFO', isin: ISIN },
]

const liveRows = (): LiveRow[] =>
  ROWS.map((row, index) => ({
    item: { id: index + 1, symbol: row.symbol, exchange: row.exchange, position: index },
    key: `${row.exchange}:${row.symbol}`,
    view: rowView(undefined, undefined, 'close'),
    direction: null,
    stale: false,
    tickAt: null,
  }))

const pref = (over: Partial<Pref> = {}): Pref => ({
  sort: 'list',
  descending: false,
  basis: 'close',
  held: false,
  ...over,
})
const viewOf = (p: Pref): View =>
  ({
    ref: 'list:1',
    kind: 'list',
    version: 'a:7',
    pref: p,
    sections: [],
    rows: ROWS.map((row) => ({
      item: 1,
      contract: { ...row, key: `${row.exchange}:${row.symbol}` },
      section_id: null,
    })),
    follow: [],
  }) as unknown as View

function Page({ readOnly = false }: { readOnly?: boolean }) {
  const data = useListView('list:1').data
  const saved = useViewPref('list:1', data)
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open View
      </button>
      <LiveList
        items={liveRows().map((row) => row.item)}
        apiKey="k"
        isOpen={() => true}
        loading={false}
        editing={false}
        listName="Mine"
        openItemId={null}
        panel={{ 'aria-label': 'Watchlist' }}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        onCloseSheet={() => setOpen(false)}
        onOrder={() => {}}
        onAlert={() => {}}
        viewPref={saved}
        viewOpen={open}
        view={data}
        readOnly={readOnly}
      />
    </>
  )
}

const server = { pref: pref() }

async function mount(p: Pref, props: { readOnly?: boolean } = {}) {
  server.pref = p
  const client = createQueryClient()
  const out = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
  await act(async () => {})
  return out
}

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  server.pref = pref()
  books.getHoldings.mockReset()
  books.getPositions.mockReset()
  books.holdings = [
    { symbol: 'RELIANCE', exchange: 'BSE', quantity: 25, product: 'CNC', isin: ISIN },
    { symbol: 'TCS', exchange: 'NSE', quantity: 0, product: 'CNC', isin: 'INE467B01029' },
  ]
  books.positions = [
    { symbol: 'NIFTY25NOVFUT', exchange: 'NFO', quantity: -30, product: 'NRML' },
    { symbol: 'RELIANCE', exchange: 'NSE', quantity: 65, product: 'MIS' },
  ]
  books.getHoldings.mockImplementation(async () => ({
    status: 'success',
    data: { holdings: books.holdings, statistics: {} },
  }))
  books.getPositions.mockImplementation(async () => ({ status: 'success', data: books.positions }))
  feed.state = {
    rows: liveRows(),
    line: null,
    now: T,
    feed: { connected: true },
    snapshot: 'ready',
    read: true,
    retry: () => {},
  }
  b().on('state', () => ({ enabled: true }))
  b().on('pref', (call) => {
    server.pref = { ...server.pref, ...(call.body as Partial<Pref>) }
    return { version: 'a:8', id: null, words: null }
  })
  b().on('view', () => viewOf(server.pref))
})
afterEach(() => vi.useRealTimers())

const row = (name: string) => screen.getByText(name).closest('li') as HTMLElement

describe('the holdings line', () => {
  it('shows the holding and the position with their words and signs, nothing for zero', async () => {
    await mount(pref({ held: true }))
    // A BSE holding on the NSE row, by ISIN, and the NSE position, signed.
    await waitFor(() => expect(within(row('RELIANCE')).getByText('Holding 25')).toBeInTheDocument())
    expect(within(row('RELIANCE')).getByText('Position +65')).toBeInTheDocument()
    // A contract matches its own position exactly, and is not given the holding of its ISIN.
    expect(within(row('NIFTY25NOVFUT')).getByText('Position −30')).toBeInTheDocument()
    expect(within(row('NIFTY25NOVFUT')).queryByText(/^Holding/)).toBeNull()
    // A holding of zero shows nothing.
    expect(within(row('TCS')).queryByText(/Holding|Position/)).toBeNull()
  })

  it('draws it in ink, never in the up or down colour, with an icon on the holding', async () => {
    await mount(pref({ held: true }))
    const line = await waitFor(() => {
      const el = row('RELIANCE').querySelector('[data-holding-line]')
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    expect(line.className).toContain('--text-primary')
    expect(line.className).not.toMatch(/--up|--down|--profit|--loss/)
    expect(line.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('reads the books only when the switch is on, and shows nothing with it off', async () => {
    await mount(pref())
    await act(async () => {})
    expect(books.getHoldings).not.toHaveBeenCalled()
    expect(books.getPositions).not.toHaveBeenCalled()
    expect(document.querySelector('[data-holding-line]')).toBeNull()
  })

  it('never shows the line, and never reads the books, in a family render', async () => {
    await mount(pref({ held: true }), { readOnly: true })
    await act(async () => {})
    expect(books.getHoldings).not.toHaveBeenCalled()
    expect(books.getPositions).not.toHaveBeenCalled()
    expect(document.querySelector('[data-holding-line]')).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Open View' }))
    expect(screen.queryByRole('switch', { name: 'Show what I hold' })).toBeNull()
  })

  it('draws today’s page with the switch off: the same second lines, no holdings parts', async () => {
    await mount(pref())
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByText(/Holding \d|Position [+−]/)).toBeNull()
  })
})

describe('the switch', () => {
  it('is 48 dp, reads Off, and sends held with the rest of the preference', async () => {
    const user = userEvent.setup()
    await mount(pref({ sort: 'price', descending: true }))
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    const sheet = screen.getByRole('dialog')
    const toggle = within(sheet).getByRole('switch', { name: 'Show what I hold' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(toggle.className).toContain('min-h-12')
    expect(within(toggle).getByText('Off')).toBeInTheDocument()
    await user.click(toggle)
    await waitFor(() => expect(b().count('pref')).toBe(1))
    expect(b().calls.find((call) => call.op === 'pref')?.body).toEqual({
      ref: 'list:1',
      sort: 'price',
      descending: true,
      basis: 'close',
      held: true,
    })
    expect(within(sheet).getByRole('switch', { name: 'Show what I hold' })).toHaveAttribute(
      'aria-checked',
      'true'
    )
    expect(within(sheet).getByText('On')).toBeInTheDocument()
  })

  it('is restored from what view says, and puts itself back with the sentence if refused', async () => {
    const user = userEvent.setup()
    await mount(pref({ held: true }))
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    const sheet = screen.getByRole('dialog')
    expect(within(sheet).getByRole('switch', { name: 'Show what I hold' })).toHaveAttribute(
      'aria-checked',
      'true'
    )
    b().on('pref', () => ({
      status: 'error',
      error: { code: 'invalid', message: 'Not saved. Try again.' },
    }))
    await user.click(within(sheet).getByRole('switch', { name: 'Show what I hold' }))
    await waitFor(() => expect(within(sheet).getByRole('alert')).toBeInTheDocument())
    expect(within(sheet).getByRole('switch', { name: 'Show what I hold' })).toHaveAttribute(
      'aria-checked',
      'true'
    )
  })
})

describe('what it costs: no request of its own on a timer', () => {
  it('makes no read while off, however long the page stays open', async () => {
    vi.useFakeTimers()
    renderHook(() => useHeld({ enabled: false, apiKey: 'k', marketOpen: () => true }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000)
    })
    expect(books.getHoldings).not.toHaveBeenCalled()
    expect(books.getPositions).not.toHaveBeenCalled()
  })

  it('reads once on opening, then only at the books’ own interval', async () => {
    vi.useFakeTimers()
    renderHook(() => useHeld({ enabled: true, apiKey: 'k', marketOpen: () => true }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(books.getHoldings).toHaveBeenCalledTimes(1)
    expect(books.getPositions).toHaveBeenCalledTimes(1)
    // Nothing in between: no timer of this hook's own, 44 seconds on.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(44_000)
    })
    expect(books.getHoldings).toHaveBeenCalledTimes(1)
    expect(books.getPositions).toHaveBeenCalledTimes(1)
    // The books' own interval (RESYNC_MS) is what reads next.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(books.getPositions).toHaveBeenCalledTimes(2)
  })
})
