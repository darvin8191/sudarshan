import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})
vi.mock('@/utils/toast', () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }))

import type { WatchlistItem } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { ArrangeSheets, useArrange } from '../ArrangeSheet'
import { OPEN_GUARD } from '../openGuard'
import { rowView } from '../rows'
import { SectionSheets } from '../SectionMenus'
import type { LiveRow } from '../useLiveRows'
import { overlayOf, useOverlay } from '../useOverlay'
import { WatchlistRows } from '../WatchlistRows'
import { BUILT_IN, draftOf, fitTo, LISTS_FULL, roomWords } from './arrange'
import type { Entry, Row, Section, View } from './types'
import { useListView } from './useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const SYMBOLS: Record<number, string> = { 1: 'AAA', 2: 'BBB', 3: 'CCC', 4: 'DDD', 5: 'EEE' }
const ITEMS: WatchlistItem[] = [1, 2, 3, 4, 5].map((id) => ({
  id,
  symbol: SYMBOLS[id],
  exchange: 'NSE',
  position: id,
}))
const ROWS: LiveRow[] = ITEMS.map((item) => ({
  item,
  key: `NSE:${item.symbol}`,
  view: rowView(undefined, undefined),
  direction: null,
  stale: false,
  tickAt: null,
}))

const contract = (symbol: string) => ({
  exchange: 'NSE',
  symbol,
  key: `NSE:${symbol}`,
  name: symbol,
  lot: null,
  tick: null,
  expiry: null,
  isin: null,
})
const row = (item: number | null, section_id: number | null, symbol = SYMBOLS[item ?? 0]): Row => ({
  item,
  contract: contract(symbol),
  section_id,
  flag: null,
  has_note: false,
  expired: false,
  metric: null,
})
const section = (id: number, name: string, position: number, count: number): Section => ({
  id,
  name,
  mark: 0,
  position,
  count,
})
const view = (over: Partial<View> = {}): View => ({
  ref: 'list:1',
  kind: 'list',
  name: 'Mine',
  version: 'a:7',
  locked: false,
  as_of: null,
  lines: [],
  total: 5,
  missing: [],
  sections: [section(11, 'IT', 2, 1), section(10, 'Banks', 1, 2)],
  rows: [row(2, 10), row(3, 10), row(4, 11), row(1, null), row(5, null)],
  follow: [],
  pref: { sort: 'manual', descending: false, basis: 'day', held: false } as View['pref'],
  ...over,
})
const entry = (id: number, name: string, count: number): Entry => ({
  ref: `list:${id}`,
  kind: 'list',
  name,
  count,
  room: 250 - count,
  locked: false,
  note: null,
})
const lists = (own: Entry[]) => ({
  version: 'a:1',
  own,
  presets: [],
  ranked: [],
  flags: [],
  favourites: [],
})
/** A list of `n` symbols, for the room tests. */
const filled = (n: number, over: Partial<View> = {}): View =>
  view({
    ref: 'list:7',
    version: 'b:3',
    sections: [section(70, 'Core', 1, 0)],
    rows: Array.from({ length: n }, (_, i) => row(100 + i, null, `Z${i}`)),
    ...over,
  })

function Harness({ editing = false }: { editing?: boolean }) {
  const data = useListView('list:1').data
  const { overlay, open, swap, close } = useOverlay(true)
  const a = useArrange({
    ref: 'list:1',
    listId: 1,
    view: data,
    items: ITEMS,
    editing,
    open: (id) => open({ kind: 'pickSection', item: id }),
  })
  return (
    <>
      <WatchlistRows
        rows={ROWS}
        loading={false}
        editing={editing}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        arrange={a.edit}
      />
      <SectionSheets
        overlay={overlay}
        view={data}
        listId={1}
        alertFor={(ref, name, count) => ({
          href: `/m?${ref}${name}${count}`,
          off: null,
          go: () => {},
        })}
        onSwap={swap}
        onClose={close}
      />
      <ArrangeSheets
        overlay={overlay}
        view={data}
        items={ITEMS}
        listId={1}
        sendTo={a.sendTo}
        sectionOf={a.sectionOf}
        onClose={close}
      />
    </>
  )
}

function setup(path = '/watchlist', editing = false, data: View = view()) {
  b().on('state', () => ({ enabled: true }))
  b().on('view', (call) => (call.params.ref === 'list:7' ? filled(0) : data))
  b().on('arrange', () => ({ version: 'a:8', id: 1, words: null }))
  b().on('copy', () => ({ list: 7, added: 1, skipped: 0, version: null, words: null }))
  b().on('lists', () => lists([entry(1, 'Mine', 5), entry(7, 'Swing', 24)]))
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter
        initialEntries={['/watchlist', path]}
        initialIndex={path === '/watchlist' ? 0 : 1}
      >
        <Harness editing={editing} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const body = (op: 'arrange' | 'copy') =>
  b()
    .calls.filter((call) => call.op === op)
    .map((call) => call.body)

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
})

describe('arranging in edit mode', () => {
  it('moves a symbol to a section and saves one arrange with the order, sections and version', async () => {
    const user = userEvent.setup()
    setup('/watchlist', true)
    await user.click(await screen.findByRole('button', { name: 'Move CCC to section' }))
    await user.click(await screen.findByRole('button', { name: 'IT' }))
    expect(body('arrange')).toEqual([])
    // The sheet closes by going back in history, which the router applies after the tap returns.
    // While it is still mounted the dialog hides the page behind it from the accessibility tree.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(body('arrange')).toHaveLength(1))
    const item = (symbol: string, section_id: number | null) => ({
      exchange: 'NSE',
      symbol,
      section_id,
    })
    expect(body('arrange')[0]).toEqual({
      id: 1,
      version: 'a:7',
      sections: [10, 11],
      items: [
        item('AAA', null),
        item('EEE', null),
        item('BBB', 10),
        item('DDD', 11),
        item('CCC', 11),
      ],
    })
  })

  it('moves a whole section up and saves the new section order', async () => {
    const user = userEvent.setup()
    setup('/watchlist', true)
    await user.click(await screen.findByRole('button', { name: 'Move section IT up' }))
    expect(screen.getByRole('button', { name: 'Move section Banks up' })).not.toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(body('arrange')).toHaveLength(1))
    const sent = body('arrange')[0] as { sections: number[]; items: { symbol: string }[] }
    expect(sent.sections).toEqual([11, 10])
    expect(sent.items.map((each) => each.symbol)).toEqual(['AAA', 'EEE', 'DDD', 'BBB', 'CCC'])
  })

  it('keeps symbol up and down inside a section, and every control at 48 dp', async () => {
    const user = userEvent.setup()
    setup('/watchlist', true)
    await screen.findByRole('button', { name: 'Save order' })
    await user.click(screen.getByRole('button', { name: 'Move BBB down' }))
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(body('arrange')).toHaveLength(1))
    expect(
      (body('arrange')[0] as { items: { symbol: string }[] }).items.map((i) => i.symbol)
    ).toEqual(['AAA', 'EEE', 'CCC', 'BBB', 'DDD'])
    for (const button of screen.getAllByRole('button'))
      expect(button.className).toMatch(/size-12|min-h-12/)
  })

  it('shows a conflict in its words and reads the list again', async () => {
    const user = userEvent.setup()
    setup('/watchlist', true)
    b().on('arrange', () => ({
      status: 'error',
      code: 'conflict',
      message: 'Changed on another screen. The list is shown again.',
    }))
    await user.click(await screen.findByRole('button', { name: 'Move section IT up' }))
    const reads = b().count('view')
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    expect(
      await screen.findByText('Changed on another screen. The list is shown again.')
    ).toBeVisible()
    await waitFor(() => expect(b().count('view')).toBeGreaterThan(reads))
  })

  it('shows the failed sentence as it comes, keeps Save for another try, and reads again', async () => {
    const user = userEvent.setup()
    setup('/watchlist', true)
    const sentence =
      'Saved here, but the order on /trading is unchanged until you arrange the list again.'
    b().on('arrange', () => ({ status: 'error', code: 'failed', message: sentence }))
    await user.click(await screen.findByRole('button', { name: 'Move section IT up' }))
    const reads = b().count('view')
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    expect(await screen.findByText(sentence)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save order' })).not.toBeDisabled()
    await waitFor(() => expect(b().count('view')).toBeGreaterThan(reads))
  })
})

describe('a removed symbol', () => {
  it('leaves the draft and the order it would save', () => {
    const draft = draftOf(view(), ITEMS)
    const rest = ITEMS.filter((each) => each.id !== 3)
    const fitted = fitTo(draft, rest)
    expect(fitted.rows.map((each) => each.id)).toEqual(
      draft.rows.map((each) => each.id).filter((id) => id !== 3)
    )
    expect(fitTo(draft, ITEMS)).toBe(draft)
  })
})

describe('move to section from the symbol sheet', () => {
  it('sends one arrange for the one change, outside edit mode', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=to-section&ci=2')
    await user.click(await screen.findByRole('button', { name: 'IT' }))
    await waitFor(() => expect(body('arrange')).toHaveLength(1))
    expect(
      (body('arrange')[0] as { items: { symbol: string; section_id: number | null }[] }).items
    ).toEqual([
      { exchange: 'NSE', symbol: 'AAA', section_id: null },
      { exchange: 'NSE', symbol: 'EEE', section_id: null },
      { exchange: 'NSE', symbol: 'CCC', section_id: 10 },
      { exchange: 'NSE', symbol: 'DDD', section_id: 11 },
      { exchange: 'NSE', symbol: 'BBB', section_id: 11 },
    ])
  })

  it('ignores taps for the first 500 ms', async () => {
    OPEN_GUARD.ms = 500
    setup('/watchlist?sheet=to-section&ci=2')
    const it = await screen.findByRole('button', { name: 'IT' })
    fireEvent.click(it)
    expect(body('arrange')).toEqual([])
    await new Promise((resolve) => setTimeout(resolve, 600))
    fireEvent.click(it)
    await waitFor(() => expect(body('arrange')).toHaveLength(1))
  })
})

describe('copy and move to another list', () => {
  it('copies one symbol to a list with its room line: one copy, no move, no version', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=to-list&ci=2')
    expect(await screen.findByRole('button', { name: 'Swing · 24 of 250' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Swing · 24 of 250' }))
    await user.click(screen.getByRole('button', { name: 'Copy 1' }))
    await waitFor(() => expect(body('copy')).toHaveLength(1))
    expect(body('copy')[0]).toEqual({
      from: { ref: 'list:1', keys: ['NSE:BBB'] },
      to: { list: 7 },
    })
  })

  it('sends the target version when a section of it is set', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=to-list&ci=2')
    await user.click(await screen.findByRole('button', { name: 'Swing · 24 of 250' }))
    await user.click(await screen.findByRole('button', { name: 'Core' }))
    await user.click(screen.getByRole('button', { name: 'Copy 1' }))
    await waitFor(() => expect(body('copy')).toHaveLength(1))
    expect(body('copy')[0]).toEqual({
      from: { ref: 'list:1', keys: ['NSE:BBB'] },
      to: { list: 7, section_id: 70 },
      version: 'b:3',
    })
  })

  it('moves a whole section from its menu with move set', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=section&section=10')
    await user.click(await screen.findByRole('button', { name: /Move to another list/ }))
    await user.click(await screen.findByRole('button', { name: 'Swing · 24 of 250' }))
    await user.click(screen.getByRole('button', { name: 'Move 2' }))
    await waitFor(() => expect(body('copy')).toHaveLength(1))
    expect(body('copy')[0]).toEqual({
      from: { ref: 'list:1', section_id: 10 },
      to: { list: 7 },
      move: true,
    })
  })

  it('copies to a new list by name', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=to-list&ci=2')
    await user.click(await screen.findByRole('button', { name: 'New list' }))
    await user.type(screen.getByLabelText('List name'), 'Fresh')
    await user.click(screen.getByRole('button', { name: 'Copy 1' }))
    await waitFor(() => expect(body('copy')).toHaveLength(1))
    expect(body('copy')[0]).toEqual({
      from: { ref: 'list:1', keys: ['NSE:BBB'] },
      to: { name: 'Fresh' },
    })
  })

  it('refuses before sending when the target has no room, in the server words', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=section&section=10')
    b().on('view', (call) => (call.params.ref === 'list:7' ? filled(249) : view()))
    b().on('lists', () => lists([entry(7, 'Swing', 249)]))
    await user.click(await screen.findByRole('button', { name: /Copy to another list/ }))
    await user.click(await screen.findByRole('button', { name: 'Swing · 249 of 250' }))
    const words =
      'Swing has room for 1 more symbol; this section has 2. Make room in Swing or pick another list.'
    expect(await screen.findByText(words)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Copy 2' })).toBeDisabled()
    expect(body('copy')).toEqual([])
    expect(roomWords('Swing', 1, 2, true)).toBe(words)
    expect(roomWords('Swing', 3, 8, true)).toBe(
      'Swing has room for 3 more symbols; this section has 8. Make room in Swing or pick another list.'
    )
  })

  // Review M6: the details step and its Copy take the list's place in the same open sheet, under the
  // finger that picked the list; the second tap of a double tap must not send.
  it('ignores taps on the details step for half a second after it takes the list’s place', async () => {
    OPEN_GUARD.ms = 500
    setup('/watchlist?sheet=to-list&ci=2')
    const swing = await screen.findByRole('button', { name: 'Swing · 24 of 250' })
    await new Promise((resolve) => setTimeout(resolve, 600)) // past the sheet's own half second
    fireEvent.click(swing)
    const copy = await screen.findByRole('button', { name: 'Copy 1' })
    fireEvent.click(copy) // the tail of a double tap
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(body('copy')).toEqual([])
    await new Promise((resolve) => setTimeout(resolve, 550))
    fireEvent.click(screen.getByRole('button', { name: 'Copy 1' }))
    await waitFor(() => expect(body('copy')).toHaveLength(1))
  })

  it('turns New list off at 50 lists and says why', async () => {
    setup('/watchlist?sheet=to-list&ci=2')
    b().on('lists', () => lists(Array.from({ length: 50 }, (_, i) => entry(i + 1, `L${i + 1}`, 1))))
    expect(await screen.findByText(LISTS_FULL)).toBeVisible()
    expect(screen.getByRole('button', { name: 'New list' })).toBeDisabled()
  })

  it('shows the server sentence for a refusal and reads the lists again', async () => {
    const user = userEvent.setup()
    setup('/watchlist?sheet=to-list&ci=2')
    b().on('copy', () => ({
      status: 'error',
      code: 'conflict',
      message: 'Changed on another screen. The list is shown again.',
    }))
    await user.click(await screen.findByRole('button', { name: 'Swing · 24 of 250' }))
    const reads = b().count('lists')
    await user.click(screen.getByRole('button', { name: 'Copy 1' }))
    expect(
      await screen.findByText('Changed on another screen. The list is shown again.')
    ).toBeVisible()
    await waitFor(() => expect(b().count('lists')).toBeGreaterThan(reads))
  })
})

describe('built-in lists and the switch off', () => {
  it('offers no arranging on a list that has no section view (a built-in one)', () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(
      () =>
        useArrange({
          ref: null,
          listId: null,
          view: undefined,
          items: ITEMS,
          editing: true,
          open: () => {},
        }),
      { wrapper }
    )
    expect(result.current.edit).toBeUndefined()
    expect(BUILT_IN).toBe('A built-in list cannot be changed. Make a copy to arrange it.')
  })

  it("draws today's edit rows with the switch off: up, down and remove only", () => {
    render(
      <WatchlistRows
        rows={ROWS}
        loading={false}
        editing
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
      />
    )
    expect(screen.getByRole('button', { name: 'Move AAA up' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /to section/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Save order/ })).toBeNull()
  })

  it('does not know the F5 sheets in the address while v2 is off', () => {
    const params = new URLSearchParams('sheet=to-list&ci=2')
    expect(overlayOf(params, null)).toBeNull()
    expect(overlayOf(params, null, true)).toEqual({
      kind: 'pickList',
      item: 2,
      section: null,
      move: false,
    })
  })
})
