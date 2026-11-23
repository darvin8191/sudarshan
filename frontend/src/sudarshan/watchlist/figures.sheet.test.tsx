/**
 * M8.3 GA6.2: choosing a list's figures in the View sheet. The saved View comes from the mock
 * bridge's `view` and each pick is saved with `pref`; the rows' live data is a fixed stand-in.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
import { LiveList } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import { rowView } from './rows'
import { useViewPref } from './ViewSheet'
import type { Figure, Pref, View } from './v2/types'
import { useListView } from './v2/useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>
const server = { cols: [] as Figure[], refuse: null as string | null }

const pref = (cols: Figure[]): Pref => ({
  sort: 'list',
  descending: false,
  basis: 'close',
  held: false,
  cols,
})

function Page() {
  const data = useListView('list:1').data
  const saved = useViewPref('list:1', data)
  const [open, setOpen] = useState(true)
  return (
    <LiveList
      items={[{ id: 1, symbol: 'INFY', exchange: 'NSE', position: 0 }]}
      apiKey="k"
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Core"
      openItemId={null}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => setOpen(false)}
      onOrder={() => {}}
      onAlert={() => {}}
      flagNames={[]}
      viewPref={saved}
      viewOpen={open}
    />
  )
}

async function mount(cols: Figure[]) {
  server.cols = cols
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <Page />
      </MemoryRouter>
    </QueryClientProvider>
  )
  await waitFor(() => expect(b().count('view')).toBeGreaterThan(0))
  await act(async () => {})
  return within(await screen.findByRole('dialog'))
}

const saves = () =>
  b()
    .calls.filter((call) => call.op === 'pref')
    .map((call) => (call.body as { cols?: Figure[] }).cols)

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  server.refuse = null
  feed.state = {
    rows: [
      {
        item: { id: 1, symbol: 'INFY', exchange: 'NSE', position: 0 },
        key: 'NSE:INFY',
        view: rowView(undefined, undefined),
        direction: null,
        stale: false,
        tickAt: null,
      },
    ],
    line: null,
    now: 0,
    feed: { connected: true, fallback: false, epoch: 1 },
    snapshot: 'ready',
    read: true,
    retry: () => {},
    readAll: async () => null,
    lines: new Map(),
    directions: new Map(),
  }
  b().on('state', () => ({ enabled: true }))
  b().on(
    'view',
    () =>
      ({
        ref: 'list:1',
        kind: 'list',
        version: 'a:7',
        pref: pref(server.cols),
        sections: [],
        rows: [],
        follow: [],
      }) as unknown as View
  )
  b().on('pref', (call) => {
    if (server.refuse)
      return { status: 'error', code: 'invalid', message: server.refuse, field: 'cols' }
    server.cols = (call.body as { cols: Figure[] }).cols
    return { version: null, id: null, words: null }
  })
})

describe('Figures in the rows (M8.3, the View sheet)', () => {
  it('saves each pick at once, in the order made, and numbers the picks', async () => {
    const user = userEvent.setup()
    const sheet = await mount([])
    expect(
      sheet.getByText(/^Pick up to six\. The first two show with the phone upright/)
    ).toBeVisible()
    await user.click(sheet.getByRole('checkbox', { name: 'Volume' }))
    await user.click(sheet.getByRole('checkbox', { name: 'Day high' }))
    await waitFor(() => expect(saves()).toEqual([['volume'], ['volume', 'high']]))
    expect(sheet.getByRole('checkbox', { name: 'Volume, place 1' })).toHaveAttribute(
      'aria-checked',
      'true'
    )
    expect(sheet.getByRole('checkbox', { name: 'Day high, place 2' })).toBeChecked()
    expect(sheet.getByRole('checkbox', { name: 'IV (options)' })).not.toBeChecked()
  })

  it('renumbers when one is taken off', async () => {
    const user = userEvent.setup()
    const sheet = await mount(['volume', 'high', 'iv'])
    await user.click(sheet.getByRole('checkbox', { name: 'Day high, place 2' }))
    await waitFor(() => expect(saves()).toEqual([['volume', 'iv']]))
    expect(sheet.getByRole('checkbox', { name: 'IV (options), place 2' })).toBeChecked()
  })

  it('refuses a seventh in place, in its words, and sends nothing', async () => {
    const user = userEvent.setup()
    const sheet = await mount(['high', 'low', 'open', 'prev', 'volume', 'oi'])
    await user.click(sheet.getByRole('checkbox', { name: 'IV (options)' }))
    expect(sheet.getByRole('status')).toHaveTextContent('Six figures at most. Remove one first.')
    expect(saves()).toEqual([])
    expect(sheet.getByRole('checkbox', { name: 'IV (options)' })).not.toBeChecked()
    await user.click(sheet.getByRole('checkbox', { name: 'Open interest (OI), place 6' }))
    expect(sheet.getByRole('status')).toHaveTextContent('')
  })

  it('puts the picks back and shows the server’s sentence when it refuses', async () => {
    const user = userEvent.setup()
    const sheet = await mount(['high'])
    server.refuse = 'Each figure can be picked once.'
    await user.click(sheet.getByRole('checkbox', { name: 'Day low' }))
    expect(await sheet.findByRole('alert')).toHaveTextContent('Each figure can be picked once.')
    expect(sheet.getByRole('checkbox', { name: 'Day low' })).not.toBeChecked()
    expect(sheet.getByRole('checkbox', { name: 'Day high, place 1' })).toBeChecked()
  })

  it('says what uses more data and what is not offered, every row 48 dp', async () => {
    const sheet = await mount([])
    expect(
      sheet.getByText(
        'Day high, Day low, Volume, OI, ATP and IV come with every tick and use more mobile data than the price alone. IV is Upstox’s; the strike sheet works its own out.'
      )
    ).toBeVisible()
    expect(sheet.getByText(/^Not offered here: OI change, 52-week high and low/)).toBeVisible()
    const rows = sheet.getAllByRole('checkbox')
    expect(rows.map((each) => each.getAttribute('aria-label'))).toEqual([
      'Day high',
      'Day low',
      'Today’s open',
      'Previous close',
      'Volume',
      'Open interest (OI)',
      'Average price (ATP)',
      'IV (options)',
    ])
    for (const row of rows) expect(row.className).toMatch(/min-h-12/)
  })

  it('closes with its close button', async () => {
    const user = userEvent.setup()
    const sheet = await mount([])
    await user.click(sheet.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('the line under the tabs (M8.3)', () => {
  it('says when fewer figures show than are picked, and nothing when all do', async () => {
    await mount(['high', 'low', 'volume', 'oi'])
    expect(
      screen.getByText('Figures: 2 of 4 shown here; turn the phone on its side for the rest.')
    ).toBeInTheDocument()
  })

  it('says nothing for two picks on a phone upright', async () => {
    await mount(['high', 'low'])
    expect(document.querySelector('[data-figures-line]')).toBeNull()
  })
})
