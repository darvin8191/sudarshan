/**
 * What a screener adds to the watchlist page (M9 UI7): an Auto tag on a list it feeds, a chip with
 * an icon and a word on each row it put there, who it let go of today, Pin and Unpin in the
 * instrument's sheet sent once with the row's own version, and a list push reading the rows again.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ membership: vi.fn(), member: vi.fn(), state: vi.fn() }))
vi.mock('@/sudarshan/screener/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/screener/api')>()),
  screenerApi: api,
}))

const live = vi.hoisted(() => ({ rows: [] as unknown[] }))
vi.mock('@/sudarshan/watchlist/useLiveRows', () => ({
  useLiveRows: () => ({
    rows: live.rows,
    line: null,
    now: Date.UTC(2026, 10, 9, 8, 0, 0),
    feed: { connected: true },
    snapshot: null,
    retry: () => {},
  }),
}))
// The sheet itself is the watchlist's own and tested there; here only what it is given matters.
vi.mock('@/sudarshan/watchlist/SymbolSheet', () => ({
  SymbolSheet: ({ row, pin }: { row: unknown; pin?: { pinned: boolean; onToggle: () => void } }) =>
    row && pin ? (
      <button type="button" onClick={pin.onToggle}>
        {pin.pinned ? 'Unpin' : 'Pin'}
      </button>
    ) : null,
}))

import { createQueryClient } from '@/app/providers'
import type { MemberRow } from '@/sudarshan/screener/api'
import { exitedToday, tagsOf, useListMembership } from '@/sudarshan/screener/feed'
import { membershipKey } from '@/sudarshan/screener/keys'
import { onListsPush } from '@/sudarshan/screener/push'
import type { Chip } from '@/sudarshan/screener/types'
import { ListTabs } from '@/sudarshan/watchlist/ListTabs'
import { LiveList } from '@/sudarshan/watchlist/LiveList'

const NOW = Date.UTC(2026, 10, 9, 8, 0, 0)
const NEW: Chip = {
  kind: 'new',
  text: 'New',
  icon: 'circle-plus',
  tone: 'brand',
  name: 'New in the list today',
  note: '',
}

function member(symbol: string, over: Partial<MemberRow> = {}): MemberRow {
  return {
    id: symbol.length * 10,
    version: 3,
    screen_id: 1,
    list_id: 7,
    symbol,
    exchange: 'NSE',
    status: 'active',
    pinned: false,
    first_seen_at: NOW - 3_600_000,
    last_seen_at: NOW,
    exited_at: null,
    chip: NEW,
    ...over,
  }
}

function liveRow(symbol: string, id: number) {
  return {
    item: { id, symbol, exchange: 'NSE', position: id },
    key: `NSE:${symbol}`,
    view: { ltp: 100, prevClose: 99, at: NOW, live: true, change: 1, changePercent: 1 },
    direction: 'up',
    stale: false,
    tickAt: null,
  }
}

let client: ReturnType<typeof createQueryClient>

function showList(members: MemberRow[], openItemId: number | null = null) {
  render(
    <QueryClientProvider client={client}>
      <LiveList
        items={[]}
        apiKey="k"
        isOpen={() => true}
        loading={false}
        editing={false}
        listName="Screener: EMA cross"
        openItemId={openItemId}
        panel={{ 'aria-label': 'List' }}
        onOpen={() => {}}
        onMove={() => {}}
        onRemove={() => {}}
        onCloseSheet={() => {}}
        onOrder={() => {}}
        onAlert={() => {}}
        members={members}
      />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  client = createQueryClient()
  for (const fn of Object.values(api)) fn.mockReset()
  live.rows = [liveRow('INFY', 1), liveRow('SBIN', 2)]
})
afterEach(() => client.clear())

describe('lists a screener feeds', () => {
  it('tags the tab of a fed list Auto, with an icon and a word, and no other', () => {
    render(
      <ListTabs
        lists={[
          { id: 7, name: 'Screener: EMA cross', position: 0, items: [] },
          { id: 8, name: 'Mine', position: 1, items: [] },
        ]}
        activeId={7}
        editing={false}
        onSelect={() => {}}
        onCreate={() => {}}
        onRename={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
        fed={new Set([7])}
      />
    )
    const tabs = screen.getAllByRole('tab')
    expect(within(tabs[0]).getByText('Auto')).toBeInTheDocument()
    expect(within(tabs[1]).queryByText('Auto')).not.toBeInTheDocument()
  })

  it('puts a chip on each row the screener added, as an icon and a word', () => {
    showList([member('INFY'), member('SBIN', { chip: null })])
    const infy = screen.getByText('INFY').closest('li') as HTMLElement
    expect(within(infy).getByRole('img', { name: NEW.name })).toBeInTheDocument()
    expect(within(infy).getByText('New')).toBeInTheDocument()
    const sbin = screen.getByText('SBIN').closest('li') as HTMLElement
    expect(within(sbin).queryByRole('img')).not.toBeInTheDocument()
  })

  it('lists who the screen let go of today, and not who left on another day', () => {
    showList([
      member('TCS', { status: 'exited', exited_at: NOW - 1000, chip: null }),
      member('WIPRO', { status: 'exited', exited_at: NOW - 2 * 86_400_000, chip: null }),
    ])
    expect(screen.getByText(/Exited today \(1\): TCS/)).toBeInTheDocument()
    expect(screen.queryByText(/WIPRO/)).not.toBeInTheDocument()
  })

  it('keys chips by exchange and symbol and leaves out rows that are gone', () => {
    const tags = tagsOf([member('INFY'), member('TCS', { status: 'exited' })])
    expect([...tags.keys()]).toEqual(['NSE:INFY'])
    expect(exitedToday([member('TCS', { status: 'exited', exited_at: NOW })], NOW)).toEqual(['TCS'])
  })
})

describe('Pin in the instrument sheet', () => {
  it('pins with the row version and shows the answer', async () => {
    api.member.mockResolvedValue(member('INFY', { pinned: true, version: 4 }))
    client.setQueryData(membershipKey(7), { rows: [member('INFY')] })
    showList([member('INFY')], 1)
    await userEvent.click(await screen.findByRole('button', { name: 'Pin' }))
    await waitFor(() => expect(api.member).toHaveBeenCalledTimes(1))
    expect(api.member).toHaveBeenCalledWith(40, 3, 'pin')
    await waitFor(() =>
      expect(client.getQueryData<{ rows: MemberRow[] }>(membershipKey(7))?.rows[0]).toMatchObject({
        pinned: true,
        version: 4,
      })
    )
  })

  it('sends one request while one is out, however often it is tapped', async () => {
    api.member.mockReturnValue(new Promise(() => {}))
    showList([member('INFY')], 1)
    await userEvent.dblClick(await screen.findByRole('button', { name: 'Pin' }))
    expect(api.member).toHaveBeenCalledTimes(1)
  })

  it('offers no Pin for an instrument the screener did not put there', () => {
    showList([member('INFY')], 2)
    expect(screen.queryByRole('button', { name: 'Pin' })).not.toBeInTheDocument()
  })

  it('offers no Pin for a row the screener has let go', () => {
    showList([member('INFY', { status: 'exited' })], 1)
    expect(screen.queryByRole('button', { name: 'Pin' })).not.toBeInTheDocument()
  })
})

function Reader() {
  useListMembership(7, true)
  return null
}

describe('a list push', () => {
  it('reads the membership again, never patching from the push', async () => {
    api.membership.mockResolvedValue({ rows: [member('INFY')] })
    render(
      <QueryClientProvider client={client}>
        <Reader />
      </QueryClientProvider>
    )
    await waitFor(() => expect(api.membership).toHaveBeenCalledTimes(1))
    expect(api.membership).toHaveBeenCalledWith({ list: 7 })
    act(() => onListsPush(client))
    await waitFor(() => expect(api.membership).toHaveBeenCalledTimes(2))
  })
})
