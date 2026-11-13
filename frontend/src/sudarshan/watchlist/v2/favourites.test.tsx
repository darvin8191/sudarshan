import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

import type { Watchlist } from '@/api/watchlist'
import { createQueryClient } from '@/app/providers'
import { AllListsSheet, FAVOURITES_FULL, PICK_HINT } from '../AllListsSheet'
import { ListStrip, ownId, tabsFor } from '../ListStrip'
import { OPEN_GUARD } from '../openGuard'
import { useOverlay } from '../useOverlay'
import type { Entry, Favourite, Lists, State } from './types'
import { useV2State } from './useListView'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const mine = (n: number): Watchlist[] =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    name: `List ${i + 1}`,
    position: i,
    items: [],
  }))
const fav = (
  slot: number,
  ref: string,
  name = ref,
  kind: Favourite['kind'] = 'list'
): Favourite => ({
  slot,
  ref,
  kind,
  name,
  count: 0,
})
const entry = (ref: string, kind: Entry['kind'], name: string): Entry => ({
  ref,
  kind,
  name,
  count: 3,
  room: null,
  locked: kind === 'preset',
  note: null,
})

/** The page's wiring of the strip and the sheet, without the rest of the page. */
function Harness({ own, fed = new Set<number>() }: { own: Watchlist[]; fed?: Set<number> }) {
  const state = useV2State().data
  const v2 = state?.enabled === true
  const { overlay, open, close, swap } = useOverlay(v2)
  const where = useLocation()
  const tabs = tabsFor(state?.favourites ?? [], own)
  return (
    <>
      <output data-testid="where">{where.search}</output>
      {v2 && (
        <ListStrip
          tabs={tabs}
          activeRef="list:1"
          fed={fed}
          onSelect={() => {}}
          onAll={() => open({ kind: 'lists' })}
        />
      )}
      {v2 && (
        <AllListsSheet
          open={overlay?.kind === 'lists'}
          onClose={close}
          fed={fed}
          activeRef="list:1"
          onPick={() => close()}
          onNew={() => swap({ kind: 'new' })}
          newOff={null}
        />
      )}
    </>
  )
}

function setup(own: Watchlist[], state: State, lists: Partial<Lists> = {}, path = '/watchlist') {
  b().on('state', () => state)
  b().on('lists', () => ({
    version: 'a:1',
    own: [],
    presets: [],
    ranked: [],
    flags: [],
    favourites: state.favourites ?? [],
    ...lists,
  }))
  b().on('favourites', () => ({ version: null, id: null, words: null }))
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter
        initialEntries={['/watchlist', path]}
        initialIndex={path === '/watchlist' ? 0 : 1}
      >
        <Harness own={own} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}
const on = (favourites: Favourite[] = []): State => ({
  enabled: true,
  versions: { lists: 'a:1', favourites: 4, flags: 1 },
  favourites,
})

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
})

describe('the strip', () => {
  it('shows the first seven lists until something is pinned, and never more than seven', () => {
    expect(tabsFor([], mine(9)).map((t) => t.ref)).toEqual(mine(7).map((l) => `list:${l.id}`))
    const eight = Array.from({ length: 8 }, (_, i) => fav(i + 1, `list:${i + 1}`))
    expect(tabsFor(eight, mine(9))).toHaveLength(7)
  })

  it('keeps slot order, drops a deleted own list and keeps a built-in one', () => {
    const tabs = tabsFor(
      [fav(2, 'list:1'), fav(1, 'preset:nifty50', 'NIFTY 50', 'preset'), fav(3, 'list:99')],
      mine(2)
    )
    expect(tabs.map((t) => t.ref)).toEqual(['preset:nifty50', 'list:1'])
    expect(ownId('list:12')).toBe(12)
    expect(ownId('preset:x')).toBeNull()
  })

  it('marks the open tab with a tick and aria-selected, and every control is 48 dp', async () => {
    setup(mine(3), on())
    const tabs = await screen.findAllByRole('tab')
    expect(tabs).toHaveLength(3)
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false')
    expect(tabs[0].querySelector('svg')).not.toBeNull()
    for (const control of [...tabs, screen.getByRole('button', { name: 'All lists' })]) {
      expect(control.className).toMatch(/\bh-12\b/)
    }
  })

  it('says a built-in list by a lock and words, not by colour', async () => {
    setup(mine(1), on([fav(1, 'preset:nifty50', 'NIFTY 50', 'preset')]))
    const tab = await screen.findByRole('tab', { name: /NIFTY 50/ })
    expect(tab).toHaveTextContent('built-in, locked')
  })
})

describe('the All lists sheet', () => {
  it('opens in the address and Back closes it first', async () => {
    const user = userEvent.setup()
    setup(mine(2), on(), { own: [entry('list:1', 'list', 'List 1')] })
    await user.click(await screen.findByRole('button', { name: 'All lists' }))
    expect(screen.getByTestId('where')).toHaveTextContent('sheet=lists')
    expect(await screen.findByText('Your lists')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^$/))
  })

  it('labels every kind of list and says what each is in words', async () => {
    setup(
      mine(1),
      on(),
      {
        own: [entry('list:1', 'list', 'List 1')],
        presets: [entry('preset:nifty50', 'preset', 'NIFTY 50')],
        ranked: [entry('ranked:oi', 'ranked', 'Index futures OI gainers')],
        flags: [entry('flag:1', 'flag', 'Red')],
      },
      '/watchlist?sheet=lists'
    )
    const built = await screen.findByRole('region', { name: 'Built-in (locked)' })
    expect(within(built).getByText(/Built-in, locked/)).toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: 'Ranked by Upstox' })).getAllByText(
        /Ranked by Upstox/
      )
    ).toHaveLength(2)
    expect(screen.getByRole('region', { name: 'Flags' })).toBeInTheDocument()
    expect(screen.getByText(PICK_HINT)).toBeInTheDocument()
  })

  it('pins a favourite and unpins it, writing the slots with the version it read', async () => {
    const user = userEvent.setup()
    setup(
      mine(2),
      on([fav(1, 'list:1', 'List 1')]),
      {
        own: [entry('list:1', 'list', 'List 1'), entry('list:2', 'list', 'List 2')],
      },
      '/watchlist?sheet=lists'
    )
    await user.click(await screen.findByRole('button', { name: 'Add to favourites: List 2' }))
    await waitFor(() => expect(b().count('favourites')).toBe(1))
    expect(b().calls.find((c) => c.op === 'favourites')?.body).toMatchObject({
      version: 4,
      slots: [
        { slot: 1, ref: 'list:1' },
        { slot: 2, ref: 'list:2' },
      ],
    })
    const star = await screen.findAllByRole('button', { name: 'Remove from favourites: List 1' })
    expect(star[0]).toHaveAttribute('aria-pressed', 'true')
    await user.click(star[0])
    await waitFor(() => expect(b().count('favourites')).toBe(2))
    expect(b().calls.filter((c) => c.op === 'favourites')[1].body).toMatchObject({
      slots: [],
    })
  })

  it('refuses an eighth favourite in words and sends nothing', async () => {
    const seven = Array.from({ length: 7 }, (_, i) => fav(i + 1, `list:${i + 1}`, `List ${i + 1}`))
    setup(
      mine(8),
      on(seven),
      { own: mine(8).map((l) => entry(`list:${l.id}`, 'list', l.name)) },
      '/watchlist?sheet=lists'
    )
    const add = await screen.findByRole('button', { name: 'Add to favourites: List 8' })
    expect(add).toBeDisabled()
    expect(screen.getByText(FAVOURITES_FULL)).toBeInTheDocument()
    expect(b().count('favourites')).toBe(0)
  })

  it('moves a favourite down by swapping slots', async () => {
    const user = userEvent.setup()
    setup(
      mine(2),
      on([fav(1, 'list:1', 'List 1'), fav(2, 'list:2', 'List 2')]),
      {
        own: [entry('list:1', 'list', 'List 1'), entry('list:2', 'list', 'List 2')],
      },
      '/watchlist?sheet=lists'
    )
    await user.click(await screen.findByRole('button', { name: 'Move down: List 1' }))
    await waitFor(() => expect(b().count('favourites')).toBe(1))
    expect(b().calls.find((c) => c.op === 'favourites')?.body).toMatchObject({
      slots: [
        { slot: 1, ref: 'list:2' },
        { slot: 2, ref: 'list:1' },
      ],
    })
  })

  it('shows the server refusal in its words when a write is refused', async () => {
    const user = userEvent.setup()
    setup(mine(2), on(), { own: [entry('list:1', 'list', 'List 1')] }, '/watchlist?sheet=lists')
    b().on('favourites', () => ({
      status: 'error',
      code: 'conflict',
      message: 'Your favourites changed on another screen.',
    }))
    await user.click(await screen.findByRole('button', { name: 'Add to favourites: List 1' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('changed on another screen')
  })

  it('ignores taps for 500 ms after it opens', async () => {
    OPEN_GUARD.ms = 500
    const user = userEvent.setup()
    setup(mine(2), on(), { own: [entry('list:1', 'list', 'List 1')] }, '/watchlist?sheet=lists')
    await user.click(await screen.findByRole('button', { name: 'Add to favourites: List 1' }))
    expect(b().count('favourites')).toBe(0)
  })
})

describe('with v2 off', () => {
  it('renders no strip and no sheet and reads only the state', async () => {
    setup(mine(2), { enabled: false }, {}, '/watchlist?sheet=lists')
    await waitFor(() => expect(b().count('state')).toBe(1))
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByRole('button', { name: 'All lists' })).toBeNull()
    expect(screen.queryByText('All lists')).toBeNull()
    expect(b().calls.every((c) => c.op === 'state')).toBe(true)
  })
})
