/**
 * F6, the page: the View sheet, the line under the tabs, a sort that is a snapshot, the saved
 * preference, and the page as it is with the switch off. The live rows are a stand-in
 * (`useLiveRows` mocked), so a test decides each tick.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

import { createQueryClient } from '@/app/providers'
import { ListTabs } from './ListTabs'
import { LiveList } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import { OPEN_UNKNOWN, rowView, type Snapshot } from './rows'
import type { LiveRow } from './useLiveRows'
import { useViewPref, ViewSheet } from './ViewSheet'
import type { Basis, Pref, View } from './v2/types'
import { useListView } from './v2/useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const NAMES = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE']
const T = 1_760_000_000_000

/** prices: ltp per symbol; every row closed yesterday at 100 and opened today at `open`. */
function rowsOf(prices: Array<number | null>, basis: Basis = 'close', open = 100): LiveRow[] {
  return prices.map((ltp, index) => {
    const snapshot: Snapshot | undefined =
      ltp === null
        ? undefined
        : {
            at: T,
            quote: {
              ltp,
              prev_close: 100,
              open,
              high: 0,
              low: 0,
              volume: 0,
              oi: 0,
              bid: 0,
              ask: 0,
            },
          }
    return {
      item: { id: index + 1, symbol: NAMES[index], exchange: 'NSE', position: index },
      key: `NSE:${NAMES[index]}`,
      view: rowView(snapshot, undefined, basis),
      direction: null,
      stale: false,
      tickAt: null,
    }
  })
}
const setFeed = (rows: LiveRow[], read = true) => {
  feed.state = {
    rows,
    line: null,
    now: T,
    feed: { connected: true },
    snapshot: 'ready',
    read,
    retry: () => {},
  }
}

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
    rows: [],
    follow: [],
  }) as unknown as View

/** The page's wiring: the saved View read from the cache, the sheet and the list over it. */
function Page({ withView = true, editing = false }: { withView?: boolean; editing?: boolean }) {
  const data = useListView('list:1').data
  const saved = useViewPref('list:1', data)
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open View
      </button>
      <LiveList
        items={rowsOf([1, 2, 3, 4, 5]).map((row) => row.item)}
        apiKey="k"
        isOpen={() => true}
        loading={false}
        editing={editing}
        listName="Mine"
        openItemId={null}
        panel={{ 'aria-label': 'Watchlist' }}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        onCloseSheet={() => setOpen(false)}
        onOrder={() => {}}
        onAlert={() => {}}
        viewPref={withView ? saved : undefined}
        viewOpen={open}
      />
    </>
  )
}

import { useState } from 'react'

/** The server: what `view` answers is what `pref` last saved. */
const server = { pref: pref() }

async function mount(p: Pref | null, props: { withView?: boolean; editing?: boolean } = {}) {
  const client = createQueryClient()
  server.pref = p ?? pref()
  const ui = () => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const out = render(ui())
  // The saved View is read before the page is looked at, as the page does it.
  if (props.withView !== false) await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
  await act(async () => {})
  return { client, ...out, again: () => out.rerender(ui()) }
}

const symbols = () =>
  screen
    .getAllByRole('listitem')
    .map((li) => li.textContent?.slice(0, 3) ?? '')
    .filter((text) => NAMES.includes(text))

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  server.pref = pref()
  b().on('state', () => ({ enabled: true }))
  b().on('pref', (call) => {
    server.pref = { ...server.pref, ...(call.body as Partial<Pref>) }
    return { version: 'a:8', id: null, words: null }
  })
  b().on('view', () => viewOf(server.pref))
})
afterEach(() => vi.useRealTimers())

describe('a sort is a snapshot', () => {
  it('orders the rows when the list opens, from the figures of that instant', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref({ sort: 'change', descending: true }))
    await waitFor(() => expect(symbols()).toEqual(['BBB', 'EEE', 'CCC', 'DDD', 'AAA']))
  })

  it('never reorders on a tick: ten minutes of them, each row changing every second', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    const { again } = await mount(pref({ sort: 'change', descending: true }))
    await waitFor(() => expect(symbols()).toEqual(['BBB', 'EEE', 'CCC', 'DDD', 'AAA']))
    for (let second = 1; second <= 600; second += 1) {
      // AAA runs far past everyone and BBB falls below them: a live sort would swap them at once.
      setFeed(
        rowsOf([101 + second, 110 - second / 10, 105 + (second % 7), 103 - (second % 5), 108])
      )
      act(() => again())
    }
    expect(symbols()).toEqual(['BBB', 'EEE', 'CCC', 'DDD', 'AAA'])
    // The figures did move: AAA now shows its own price at its old place.
    expect(screen.getByText('701.00')).toBeInTheDocument()
  })

  it('reorders once on Sort again, and the line says when', async () => {
    const user = userEvent.setup()
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    const { again } = await mount(pref({ sort: 'change', descending: true }))
    await waitFor(() => expect(symbols()[0]).toBe('BBB'))
    expect(screen.getByText(/^Sorted by % change, high first, at \d\d:\d\d$/)).toBeInTheDocument()
    setFeed(rowsOf([150, 110, 105, 103, 108]))
    act(() => again())
    expect(symbols()[0]).toBe('BBB')
    await user.click(screen.getByRole('button', { name: 'Sort again' }))
    expect(symbols()).toEqual(['AAA', 'BBB', 'EEE', 'CCC', 'DDD'])
    setFeed(rowsOf([100, 110, 105, 103, 108]))
    act(() => again())
    expect(symbols()).toEqual(['AAA', 'BBB', 'EEE', 'CCC', 'DDD'])
  })

  it('waits for the first read of prices, and then ranks once, whatever the ticks do', async () => {
    setFeed(rowsOf([null, null, null, null, null]), false)
    const { again } = await mount(pref({ sort: 'price', descending: true }))
    expect(symbols()).toEqual(NAMES)
    setFeed(rowsOf([5, 9, 7, null, 8]), true)
    act(() => again())
    await waitFor(() => expect(symbols()).toEqual(['BBB', 'EEE', 'CCC', 'AAA', 'DDD']))
  })

  it('goes by name without any prices, and shows no Sort again for it', async () => {
    setFeed(rowsOf([null, null, null, null, null]), false)
    await mount(pref({ sort: 'name', descending: true }))
    await waitFor(() => expect(symbols()).toEqual(['EEE', 'DDD', 'CCC', 'BBB', 'AAA']))
    expect(screen.getByText('Sorted by name, Z to A')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sort again' })).toBeNull()
  })

  it('leaves the list\u2019s own order while editing', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref({ sort: 'change', descending: true }), { editing: true })
    expect(screen.queryByRole('button', { name: 'Sort again' })).toBeNull()
    expect(screen.getAllByText(/^[A-E]{3}$/).map((node) => node.textContent)).toEqual(NAMES)
  })
})

describe('change from today\u2019s open', () => {
  it('says so under the tabs, with no sort line when the order is the list\u2019s', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108], 'open', 100))
    await mount(pref({ basis: 'open' }))
    expect(await screen.findByText('Change from today\u2019s open')).toBeInTheDocument()
    expect(screen.queryByText(/^Sorted by/)).toBeNull()
    expect(symbols()).toEqual(NAMES)
  })

  it('shows a dash and the words for a row whose open is not known, and ranks it last', async () => {
    const rows = rowsOf([101, 110, 105, 103, 108], 'open', 100)
    rows[1] = rowsOf([110], 'open', 0)[0]
    rows[1].item = { id: 2, symbol: 'BBB', exchange: 'NSE', position: 1 }
    rows[1].key = 'NSE:BBB'
    setFeed(rows)
    await mount(pref({ sort: 'change', descending: true, basis: 'open' }))
    await waitFor(() => expect(symbols()).toEqual(['EEE', 'CCC', 'DDD', 'AAA', 'BBB']))
    const bbb = screen.getByText('BBB').closest('li') as HTMLElement
    expect(within(bbb).getByText(OPEN_UNKNOWN)).toBeInTheDocument()
    expect(bbb.textContent).toContain('\u2014')
  })
})

describe('the View sheet and the saved preference', () => {
  it('sends the pick with pref, for its own list, with the holdings switch as it was saved', async () => {
    const user = userEvent.setup()
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref())
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    const sheet = screen.getByRole('dialog')
    await user.click(within(sheet).getByRole('button', { name: '% change' }))
    await waitFor(() => expect(b().count('pref')).toBe(1))
    expect(
      b()
        .calls.filter((call) => call.op === 'pref')
        .at(-1)?.body
    ).toEqual({
      ref: 'list:1',
      sort: 'change',
      descending: true,
      basis: 'close',
      held: false,
    })
    await user.click(within(sheet).getByRole('button', { name: 'Low first' }))
    await user.click(within(sheet).getByRole('button', { name: 'Today\u2019s open' }))
    await waitFor(() => expect(b().count('pref')).toBe(3))
    expect(
      b()
        .calls.filter((call) => call.op === 'pref')
        .at(-1)?.body
    ).toEqual({
      ref: 'list:1',
      sort: 'change',
      descending: false,
      basis: 'open',
      held: false,
    })
  })

  it('shows the pick at once, by name, with the pressed choice carrying a check as well as a state', async () => {
    const user = userEvent.setup()
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref())
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    const sheet = screen.getByRole('dialog')
    await user.click(within(sheet).getByRole('button', { name: 'Price' }))
    const price = within(sheet).getByRole('button', { name: 'Price' })
    expect(price).toHaveAttribute('aria-pressed', 'true')
    expect(price.querySelector('svg')).not.toBeNull()
    expect(within(sheet).getByRole('button', { name: 'High first' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    expect(within(sheet).getByRole('button', { name: 'List order' })).toHaveAttribute(
      'aria-pressed',
      'false'
    )
  })

  it('restores the saved choice from view, on any device', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref({ sort: 'price', descending: false, basis: 'open' }))
    await waitFor(() => expect(symbols()).toEqual(['AAA', 'DDD', 'CCC', 'EEE', 'BBB']))
    expect(screen.getByText(/^Sorted by price, low first/)).toBeInTheDocument()
    expect(b().count('pref')).toBe(0)
  })

  it('puts the choice back and says why when the server refuses it', async () => {
    const user = userEvent.setup()
    b().on('pref', () => ({
      status: 'error',
      code: 'conflict',
      message: 'Changed on another screen.',
    }))
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref())
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    await user.click(screen.getByRole('button', { name: 'Name' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Changed on another screen.')
    expect(screen.getByRole('button', { name: 'List order' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  it('says "Open not known yet" in the sheet while some opens are missing', async () => {
    const user = userEvent.setup()
    const rows = rowsOf([101, 110, 105, 103, 108], 'open', 100)
    rows[0] = { ...rows[0], view: rowView(undefined, undefined, 'open') }
    setFeed(rows)
    await mount(pref({ basis: 'open' }))
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    expect(
      within(screen.getByRole('dialog')).getByText(/Open not known yet for 1 of 5 symbols/)
    ).toBeInTheDocument()
  })
})

describe('with the switch off', () => {
  it('renders today\u2019s page: no line, no sort, no sheet, the list in its own order', async () => {
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(null, { withView: false })
    expect(symbols()).toEqual(NAMES)
    expect(screen.queryByTestId('view-line')).toBeNull()
    expect(document.querySelector('[data-view-line]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Open View' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows no View in the list menu without the page\u2019s entry', async () => {
    const user = userEvent.setup()
    render(
      <ListTabs
        lists={[{ id: 1, name: 'Mine', position: 0, items: [] }]}
        activeId={1}
        editing={false}
        onSelect={() => {}}
        onCreate={() => {}}
        onRename={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
      />
    )
    await user.click(screen.getByRole('button', { name: 'List options' }))
    expect(screen.queryByRole('menuitem', { name: 'View' })).toBeNull()
  })

  it('offers View in the list menu of an own list in v2', async () => {
    const user = userEvent.setup()
    const onView = vi.fn()
    render(
      <ListTabs
        lists={[{ id: 1, name: 'Mine', position: 0, items: [] }]}
        activeId={1}
        editing={false}
        onSelect={() => {}}
        onCreate={() => {}}
        onRename={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
        strip={<div />}
        onView={onView}
      />
    )
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'View' }))
    expect(onView).toHaveBeenCalledOnce()
  })
})

describe('48 dp and the open guard', () => {
  it('gives every control of the sheet and the line at least 48 dp', async () => {
    const user = userEvent.setup()
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    await mount(pref({ sort: 'change', descending: true }))
    expect(screen.getByRole('button', { name: 'Sort again' }).className).toMatch(/\bmin-h-12\b/)
    await user.click(screen.getByRole('button', { name: 'Open View' }))
    const sheet = screen.getByRole('dialog')
    const buttons = within(sheet).getAllByRole('button')
    expect(buttons.length).toBeGreaterThan(8)
    for (const control of buttons) expect(control.className).toMatch(/\b(min-h-12|size-12)\b/)
  })

  it('ignores a tap on the sheet for half a second after it opens', async () => {
    OPEN_GUARD.ms = 500
    setFeed(rowsOf([101, 110, 105, 103, 108]))
    render(
      <QueryClientProvider client={createQueryClient()}>
        <ViewSheet
          open
          onClose={() => {}}
          pref={pref()}
          onChange={onChange}
          problem={null}
          noOpen={0}
          total={5}
        />
      </QueryClientProvider>
    )
    fireEvent.click(screen.getByRole('button', { name: 'Price' }))
    expect(onChange).not.toHaveBeenCalled()
    await new Promise((done) => setTimeout(done, 550))
    fireEvent.click(screen.getByRole('button', { name: 'Price' }))
    expect(onChange).toHaveBeenCalledOnce()
  })
})
const onChange = vi.fn()
