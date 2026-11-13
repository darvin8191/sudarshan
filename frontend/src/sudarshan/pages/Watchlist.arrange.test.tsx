/**
 * Review M4 on the page: edit mode with the switch on holds its moves as a draft until Save order.
 * Done, Done editing, a tab and All lists ask first while moves are not saved (Save order, Discard
 * moves, Keep editing), Back keeps the moves, a refused save stays in edit mode with its sentence,
 * and the Save bar stays on screen. The bridge is the v2 mock; the lists API is an in-memory stand-in.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))

const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<
    typeof import('@/sudarshan/watchlist/v2/mockBridge').createBridge
  >,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('@/sudarshan/watchlist/v2/mockBridge')
  bridge.current = createBridge()
  return { webClient: bridge.current.client }
})

type Item = { id: number; symbol: string; exchange: string; position: number }
const own = vi.hoisted(() => ({
  lists: [] as Array<{ id: number; name: string; position: number; items: Item[] }>,
}))
vi.mock('@/api/watchlist', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/watchlist')>()),
  watchlistApi: { list: vi.fn(async () => own.lists) },
}))
// One object for every call: a new one each render would be a new calendar each render.
const calendar = vi.hoisted(() => ({
  value: { isLoading: false, error: null, isMarketOpen: () => true, isAnyMarketOpen: () => true },
}))
vi.mock('@/hooks/useMarketStatus', () => ({ useMarketStatus: () => calendar.value }))
vi.mock('@/api/trading', () => ({
  tradingApi: { getMultiQuotes: vi.fn(async () => ({ status: 'success', results: [] })) },
}))
vi.mock('@/sudarshan/watchlist/api', () => ({
  searchSymbols: vi.fn(),
  contractOf: vi.fn(async () => ({ lotSize: 1, tickSize: 0.05 })),
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: vi.fn(async () => ({ status: 'success', underlyings: [] })) },
}))

import { createQueryClient } from '@/app/providers'
import { useAuthStore } from '@/stores/authStore'
import { OPEN_GUARD } from '@/sudarshan/watchlist/openGuard'
import type { View } from '@/sudarshan/watchlist/v2/types'
import Watchlist from './Watchlist'

const b = () => bridge.current as NonNullable<typeof bridge.current>

const item = (id: number, symbol: string, position: number): Item => ({
  id,
  symbol,
  exchange: 'NSE',
  position,
})
const entry = (id: number, name: string, count: number) => ({
  ref: `list:${id}`,
  kind: 'list',
  name,
  count,
  room: 250 - count,
  locked: false,
  note: null,
})

/** The phone's Back, as the router has it. */
function Back() {
  const navigate = useNavigate()
  return (
    <button type="button" onClick={() => navigate(-1)}>
      Phone back
    </button>
  )
}

function mount() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={['/watchlist']}>
        <Back />
        <Routes>
          <Route path="/watchlist" element={<Watchlist />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const arranged = () => b().calls.filter((call) => call.op === 'arrange')
const question = () => screen.queryByRole('dialog', { name: 'Save the new order?' })

/** Into edit mode on Main, and SBIN one place down: a move not saved yet. */
async function moveOne(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('tab', { name: /Main/ })
  await user.click(screen.getByRole('button', { name: 'List options' }))
  await user.click(await screen.findByRole('menuitem', { name: 'Reorder or remove' }))
  await user.click(await screen.findByRole('button', { name: 'Move SBIN down' }))
  await screen.findByText('The new order is not saved yet.')
}

beforeEach(() => {
  b().reset()
  OPEN_GUARD.ms = 0
  localStorage.clear()
  useAuthStore.setState({ apiKey: 'key' })
  own.lists = [
    {
      id: 1,
      name: 'Main',
      position: 0,
      items: [item(11, 'SBIN', 0), item(12, 'INFY', 1), item(13, 'TCS', 2)],
    },
    { id: 2, name: 'Swing', position: 1, items: [item(21, 'ITC', 0)] },
  ]
  b().on('state', () => ({
    enabled: true,
    versions: { lists: 'a:1', favourites: 1, flags: 1 },
    flag_names: [],
    favourites: [],
  }))
  b().on('lists', () => ({
    version: 'a:1',
    own: [entry(1, 'Main', 3), entry(2, 'Swing', 1)],
    presets: [],
    ranked: [],
    flags: [],
    favourites: [],
  }))
  b().on('view', (call) => {
    const ref = (call.params as { ref: string }).ref
    return {
      ref,
      kind: 'list',
      name: ref === 'list:2' ? 'Swing' : 'Main',
      version: '7',
      locked: false,
      as_of: null,
      lines: [],
      total: 3,
      missing: [],
      sections: [],
      rows: [],
      follow: [],
      pref: { sort: 'list', descending: false, basis: 'close', held: false },
    } satisfies Partial<View>
  })
  b().on('arrange', (call) => {
    const sent = call.body as { items: Array<{ symbol: string }> }
    const main = own.lists[0]
    main.items = sent.items.map((each, position) => ({
      ...(main.items.find((one) => one.symbol === each.symbol) as Item),
      position,
    }))
    return { version: '8', id: 1, words: null }
  })
})

describe('leaving edit mode with moves not saved', () => {
  it('Done asks first, and Save order sends the one arrange and ends edit mode', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    expect(asked).toHaveTextContent('You moved symbols in Main and have not saved the new order.')
    expect(arranged()).toHaveLength(0)
    await user.click(within(asked).getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(arranged()).toHaveLength(1))
    expect(arranged()[0].body).toMatchObject({
      id: 1,
      version: '7',
      items: [{ symbol: 'INFY' }, { symbol: 'SBIN' }, { symbol: 'TCS' }],
    })
    await waitFor(() => expect(question()).toBeNull())
    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull()
  })

  it('Discard moves sends nothing, ends edit mode and leaves the order as it was', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    await user.click(within(asked).getByRole('button', { name: 'Discard moves' }))
    await waitFor(() => expect(question()).toBeNull())
    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument()
    expect(arranged()).toHaveLength(0)
    // In again: the draft starts from the list as saved, SBIN first.
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Reorder or remove' }))
    expect(await screen.findByRole('button', { name: 'Move SBIN up' })).toBeDisabled()
    expect(screen.queryByText('The new order is not saved yet.')).toBeNull()
  })

  it('Keep editing, and the phone’s Back, stay in edit mode with the moves', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    let asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    await user.click(within(asked).getByRole('button', { name: 'Keep editing' }))
    await waitFor(() => expect(question()).toBeNull())
    expect(screen.getByText('The new order is not saved yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move SBIN up' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'Done' }))
    asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    fireEvent.click(screen.getByRole('button', { name: 'Phone back', hidden: true }))
    await waitFor(() => expect(question()).toBeNull())
    expect(screen.getByText('The new order is not saved yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
    expect(arranged()).toHaveLength(0)
  })

  it('Done editing in the list menu asks too', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Done editing' }))
    expect(await screen.findByRole('dialog', { name: 'Save the new order?' })).toBeInTheDocument()
    expect(screen.getByText('The new order is not saved yet.')).toBeInTheDocument()
  })

  it('a tab asks first, and Discard moves opens that list', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('tab', { name: /Swing/ }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    // Behind the question (hidden from the reader while it is open), Main is still the list on show.
    expect(screen.getByRole('tab', { name: /Main/, hidden: true })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    await user.click(within(asked).getByRole('button', { name: 'Discard moves' }))
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Swing/ })).toHaveAttribute('aria-selected', 'true')
    )
    expect(await screen.findByRole('button', { name: /^ITC/ })).toBeInTheDocument()
    expect(arranged()).toHaveLength(0)
  })

  it('a list picked in All lists asks first, and Save order saves Main before opening it', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: /All lists/ }))
    const block = await screen.findByRole('region', { name: 'Your lists' })
    await user.click(within(block).getByRole('button', { name: /^Swing/ }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    await user.click(within(asked).getByRole('button', { name: 'Save order' }))
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Swing/ })).toHaveAttribute('aria-selected', 'true')
    )
    expect(arranged()).toHaveLength(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('ends at once, asking nothing, when nothing was moved', async () => {
    const user = userEvent.setup()
    mount()
    await screen.findByRole('tab', { name: /Main/ })
    await user.click(screen.getByRole('button', { name: 'List options' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Reorder or remove' }))
    await user.click(await screen.findByRole('button', { name: 'Done' }))
    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument()
    expect(question()).toBeNull()
    expect(arranged()).toHaveLength(0)
  })

  it('stays in edit mode with the moves and the server’s sentence when the save is refused', async () => {
    const said =
      'Saved here, but the order on /trading is unchanged until you arrange the list again.'
    b().on('arrange', () => ({ status: 'error', code: 'failed', message: said }))
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    await user.click(screen.getByRole('button', { name: 'Done' }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    await user.click(within(asked).getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(question()).toBeNull())
    expect(await screen.findByText(said)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
    expect(screen.getByText('The new order is not saved yet.')).toBeInTheDocument()
  })

  it('ignores a tap on the question for half a second after it opens', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    OPEN_GUARD.ms = 500
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    const asked = await screen.findByRole('dialog', { name: 'Save the new order?' })
    fireEvent.click(within(asked).getByRole('button', { name: 'Save order' }))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(arranged()).toHaveLength(0)
    await new Promise((resolve) => setTimeout(resolve, 550))
    fireEvent.click(within(asked).getByRole('button', { name: 'Save order' }))
    await waitFor(() => expect(arranged()).toHaveLength(1))
  })
})

describe('the Save bar', () => {
  it('stays on screen above the bottom bar while the list scrolls (sticky, in a card that clips)', async () => {
    const user = userEvent.setup()
    mount()
    await moveOne(user)
    const bar = document.querySelector('[data-save-bar]') as HTMLElement
    expect(within(bar).getByRole('button', { name: 'Save order' })).toBeEnabled()
    expect(bar.className).toMatch(/\bsticky\b/)
    expect(bar.className).toContain('bottom-[calc(4.75rem+env(safe-area-inset-bottom))]')
    expect(bar.className).toMatch(/\bmd:bottom-0\b/)
    // overflow-hidden on the card would hold a sticky bar at the end of the list.
    const card = bar.closest('section') as HTMLElement
    expect(card.className).toMatch(/\boverflow-clip\b/)
    expect(card.className).not.toMatch(/\boverflow-hidden\b/)
  })
})
