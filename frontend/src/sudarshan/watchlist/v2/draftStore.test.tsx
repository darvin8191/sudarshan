import { QueryClientProvider } from '@tanstack/react-query'
import { render, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import type { WatchlistItem } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { istDay } from '@/sudarshan/dashboard/live'
import { useArrange } from '../ArrangeSheet'
import { WatchlistRows } from '../WatchlistRows'
import { type Draft, draftOf } from './arrange'
import {
  DRAFT_KEY,
  draftFor,
  dropDraft,
  droppedWords,
  type KeptDraft,
  keepDraft,
  readDraft,
  useDraftReturn,
} from './draftStore'
import type { View } from './types'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const ITEMS: WatchlistItem[] = ['AAA', 'BBB', 'CCC'].map((symbol, i) => ({
  id: i + 1,
  symbol,
  exchange: 'NSE',
  position: i,
}))

const view = (version = 'a:7'): View => ({
  ref: 'list:1',
  kind: 'list',
  name: 'Swing',
  version,
  locked: false,
  as_of: null,
  lines: [],
  total: 3,
  missing: [],
  sections: [],
  rows: ITEMS.map((item) => ({
    item: item.id,
    contract: {
      exchange: 'NSE',
      symbol: item.symbol,
      key: `NSE:${item.symbol}`,
      name: item.symbol,
      lot: null,
      tick: null,
      expiry: null,
      isin: null,
    },
    section_id: null,
    flag: null,
    has_note: false,
    expired: false,
    metric: null,
  })),
  follow: [],
  pref: { sort: 'list', descending: false, basis: 'close', held: false },
})

const moved: Draft = {
  sections: [],
  rows: [
    { id: 2, exchange: 'NSE', symbol: 'BBB', section: null },
    { id: 1, exchange: 'NSE', symbol: 'AAA', section: null },
    { id: 3, exchange: 'NSE', symbol: 'CCC', section: null },
  ],
}
const kept = (over: Partial<KeptDraft> = {}): KeptDraft => ({
  listId: 1,
  version: 'a:7',
  day: istDay(Date.now()),
  draft: moved,
  base: draftOf(view(), ITEMS),
  ...over,
})

beforeEach(() => {
  b().reset()
  b().on('arrange', () => ({ version: 'a:8', id: 1, words: null }))
})

afterEach(() => {
  vi.restoreAllMocks()
  dropDraft()
})

describe('the kept draft (GA6.4)', () => {
  it('is one entry in this tab, under the key the test setup clears', () => {
    expect(DRAFT_KEY).toBe('sudarshan-watchlist-draft')
    expect(readDraft()).toBeNull()
    keepDraft(kept())
    expect(JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? 'null')).toEqual(kept())
    expect(readDraft()).toEqual(kept())
    dropDraft(2) // another list's drop leaves it
    expect(readDraft()).toEqual(kept())
    dropDraft(1)
    expect(readDraft()).toBeNull()
  })

  it('stands only on the same list, version and day in India', () => {
    keepDraft(kept())
    const now = Date.now()
    expect(draftFor(1, 'a:7', now)).toEqual(kept())
    expect(draftFor(1, 'a:8', now)).toBeNull()
    expect(draftFor(2, 'a:7', now)).toBeNull()
    keepDraft(kept({ day: '2026-11-22' }))
    expect(draftFor(1, 'a:7', Date.parse('2026-11-23T04:00:00Z'))).toBeNull()
  })

  it('reads anything else in its place as none', () => {
    sessionStorage.setItem(DRAFT_KEY, '{"listId":1,"version":"a:7"}')
    expect(readDraft()).toBeNull()
    sessionStorage.setItem(DRAFT_KEY, 'not json')
    expect(readDraft()).toBeNull()
  })

  it('is kept in memory for the visit when storage throws', () => {
    const refuse = () => {
      throw new DOMException('refused', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(refuse)
    keepDraft(kept())
    expect(readDraft()).toEqual(kept())
    expect(draftFor(1, 'a:7', Date.now())).toEqual(kept())
    dropDraft()
    expect(readDraft()).toBeNull()
  })

  it('never leaves an older draft to be read back when storage is full', () => {
    keepDraft(kept())
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError')
    })
    keepDraft(kept({ draft: draftOf(view(), ITEMS) }))
    expect(readDraft()).toBeNull()
  })
})

function Harness({ editing, version = 'a:7' }: { editing: boolean; version?: string }) {
  const a = useArrange({
    ref: 'list:1',
    listId: 1,
    view: view(version),
    items: ITEMS,
    editing,
    open: () => {},
  })
  const rows = ITEMS.map((item) => ({
    item,
    key: `NSE:${item.symbol}`,
    view: {
      ltp: null,
      prevClose: null,
      at: null,
      live: false,
      change: null,
      changePercent: null,
      openMissing: false,
      note: null,
    },
    direction: null,
    stale: false,
    tickAt: null,
  }))
  return (
    <WatchlistRows
      rows={rows}
      loading={false}
      editing={editing}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      arrange={a.edit}
    />
  )
}

function mount(editing: boolean, version?: string) {
  const client = createQueryClient()
  const ui = (on: boolean, v?: string) => (
    <QueryClientProvider client={client}>
      <Harness editing={on} version={v} />
    </QueryClientProvider>
  )
  const out = render(ui(editing, version))
  return { ...out, again: (on: boolean, v?: string) => out.rerender(ui(on, v)) }
}

const order = () =>
  screen.getAllByRole('button', { name: /^Move \w+ up$/ }).map((each) => each.ariaLabel)

describe('edit mode keeps its moves across leaving the page (GA6.4)', () => {
  it('keeps them through an unmount and starts from them on return', async () => {
    const user = userEvent.setup()
    const first = mount(true)
    await user.click(await screen.findByRole('button', { name: 'Move AAA down' }))
    expect(readDraft()?.draft.rows.map((row) => row.symbol)).toEqual(['BBB', 'AAA', 'CCC'])
    first.unmount() // the bottom bar: the page goes, edit mode never ended here
    expect(readDraft()).not.toBeNull()

    mount(true)
    expect(await screen.findByText('The new order is not saved yet.')).toBeInTheDocument()
    expect(order()).toEqual(['Move BBB up', 'Move AAA up', 'Move CCC up'])
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(b().count('arrange')).toBe(1))
    expect(
      (b().calls[0].body as { items: { symbol: string }[] }).items.map((i) => i.symbol)
    ).toEqual(['BBB', 'AAA', 'CCC'])
    await waitFor(() => expect(readDraft()).toBeNull()) // Save order drops it
  })

  it('drops them when the moves go back to the list as it was', async () => {
    const user = userEvent.setup()
    mount(true)
    await user.click(await screen.findByRole('button', { name: 'Move AAA down' }))
    expect(readDraft()).not.toBeNull()
    await user.click(screen.getByRole('button', { name: 'Move AAA up' }))
    expect(readDraft()).toBeNull()
  })

  it('drops them when edit mode ends on the page (Discard moves, Done)', async () => {
    const user = userEvent.setup()
    const page = mount(true)
    await user.click(await screen.findByRole('button', { name: 'Move AAA down' }))
    expect(readDraft()).not.toBeNull()
    page.again(false)
    expect(readDraft()).toBeNull()
  })

  it('does not drop a kept draft just because the page opened out of edit mode', () => {
    keepDraft(kept())
    mount(false)
    expect(readDraft()).toEqual(kept())
  })

  it('starts from the list, not the kept moves, when the version moved', async () => {
    keepDraft(kept())
    mount(true, 'a:9')
    await screen.findByRole('button', { name: 'Save order' })
    expect(screen.queryByText('The new order is not saved yet.')).toBeNull()
    expect(order()).toEqual(['Move AAA up', 'Move BBB up', 'Move CCC up'])
  })
})

describe('the page coming back to a kept draft (GA6.4)', () => {
  const run = (over: Partial<Parameters<typeof useDraftReturn>[0]> = {}) => {
    const restore = vi.fn()
    const dropped = vi.fn()
    const reread = vi.fn()
    const props = {
      settled: true,
      v2: true,
      listId: 1 as number | null,
      listIds: [1, 2],
      listName: 'Swing',
      view: { version: 'a:7', readAt: Date.now() + 1 },
      reread,
      restore,
      dropped,
      ...over,
    }
    const hook = renderHook((p: typeof props) => useDraftReturn(p), { initialProps: props })
    return { restore, dropped, reread, hook, props }
  }

  it('turns edit mode on again for the same list and version', () => {
    keepDraft(kept())
    const { restore, dropped } = run()
    expect(restore).toHaveBeenCalledOnce()
    expect(dropped).not.toHaveBeenCalled()
    expect(readDraft()).toEqual(kept())
  })

  it('drops it with a sentence when the list was changed on another screen', () => {
    keepDraft(kept())
    const { restore, dropped } = run({ view: { version: 'a:9', readAt: Date.now() + 1 } })
    expect(restore).not.toHaveBeenCalled()
    expect(dropped).toHaveBeenCalledWith(
      'Your unsaved moves in Swing were dropped: the list was changed on another screen.'
    )
    expect(droppedWords('Swing')).toBe(dropped.mock.calls[0][0])
    expect(readDraft()).toBeNull()
  })

  it.each([
    ['an earlier day', { day: '2026-11-01' }, {}],
    ['a list that is gone', {}, { listIds: [2], listId: 2 }],
    ['the switch off', {}, { v2: false }],
    ['another list opening', {}, { listId: 2 }],
  ])('drops it without a word for %s', (_, keptOver, propsOver) => {
    keepDraft(kept(keptOver))
    const { restore, dropped } = run(propsOver)
    expect(restore).not.toHaveBeenCalled()
    expect(dropped).not.toHaveBeenCalled()
    expect(readDraft()).toBeNull()
  })

  it('waits for the lists, then judges the list on a read made since the page opened', () => {
    keepDraft(kept())
    const { restore, reread, hook, props } = run({
      settled: false,
      view: { version: 'a:7', readAt: 1 },
    })
    expect(reread).not.toHaveBeenCalled()
    hook.rerender({ ...props, settled: true })
    expect(reread).toHaveBeenCalledOnce() // a cached read: read again first
    expect(restore).not.toHaveBeenCalled()
    hook.rerender({ ...props, settled: true, view: { version: 'a:7', readAt: 2 } })
    expect(reread).toHaveBeenCalledOnce()
    hook.rerender({ ...props, settled: true, view: { version: 'a:7', readAt: Date.now() + 5 } })
    expect(restore).toHaveBeenCalledOnce()
  })
})
