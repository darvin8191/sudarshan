/**
 * A leg's own sheet on the options screen (M11.1, design 5.5): its Buy and Sell stay shut until the
 * leg's lot size is read, as on every symbol sheet, and "Add to watchlist" adds the leg through the
 * watchlists' own add. Every read is answered here; nothing is sent anywhere.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const lot = vi.hoisted(() => ({
  asked: [] as string[],
  answer: null as null | ((value: { lotSize: number; tickSize: number }) => void),
}))
vi.mock('@/sudarshan/watchlist/api', async (original) => ({
  ...(await original<typeof import('@/sudarshan/watchlist/api')>()),
  contractOf: (_key: string, symbol: string) => {
    lot.asked.push(symbol)
    return new Promise((resolve) => {
      lot.answer = resolve
    })
  },
}))
const lists = vi.hoisted(() => ({
  held: [] as Array<{
    id: number
    name: string
    position: number
    items: Array<{ id: number; symbol: string; exchange: string; position: number }>
  }>,
  added: [] as Array<[number, string, string]>,
  refuse: null as string | null,
}))
vi.mock('@/api/watchlist', async (original) => ({
  ...(await original<typeof import('@/api/watchlist')>()),
  watchlistApi: {
    list: async () => lists.held,
    addItem: async (id: number, symbol: string, exchange: string) => {
      if (lists.refuse) throw { response: { data: { message: lists.refuse } } }
      lists.added.push([id, symbol, exchange])
      return { id: 900, symbol, exchange, position: 9 }
    },
  },
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: async () => ({ status: 'success', underlyings: [] }) },
}))
const said = vi.hoisted(() => ({ success: [] as string[] }))
vi.mock('@/utils/toast', async (original) => ({
  ...(await original<typeof import('@/utils/toast')>()),
  toast: { success: (words: string) => said.success.push(words) },
}))

import type { OrderRequest } from '@/sudarshan/watchlist/SymbolSheet'
import { chainOf, EXPIRY, NOW, symbolOf } from './fixtures'
import { LegSheets } from './LegSheets'
import { chainModel } from './model'
import type { Feed } from './StrikeEntry'

const model = chainModel({ snapshot: chainOf(), lines: new Map(), followed: null, now: NOW })
const feed: Feed = {
  lines: new Map(),
  directions: new Map(),
  feed: { connected: true, fallback: false, epoch: 1 },
  now: NOW,
  marketOpen: true,
}
const PUT = symbolOf(25_900, 'PE')
const orders: OrderRequest[] = []

/** The page's part: which of the two sheets is open. */
function Host() {
  const [open, setOpen] = useState<'leg' | 'add' | null>('leg')
  const leg = { strike: 25_900, side: 'pe' as const }
  return (
    <LegSheets
      model={model}
      expiry={EXPIRY}
      leg={open === 'leg' ? leg : null}
      adding={open === 'add' ? leg : null}
      alert={null}
      apiKey="test-key"
      feed={feed}
      onClose={() => setOpen(null)}
      onOrder={(order) => orders.push(order)}
      onAlert={() => {}}
      onAddToList={() => setOpen('add')}
    />
  )
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Host />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

const item = (id: number, symbol: string) => ({ id, symbol, exchange: 'NFO', position: id })

beforeEach(() => {
  lot.asked = []
  lot.answer = null
  lists.held = [
    { id: 1, name: 'Swing', position: 0, items: [item(11, 'TESTFUT')] },
    { id: 2, name: 'Hedges', position: 1, items: [item(21, PUT)] },
    {
      id: 3,
      name: 'Crowded',
      position: 2,
      items: Array.from({ length: 250 }, (_, i) => item(100 + i, `TEST${i}`)),
    },
  ]
  lists.added = []
  lists.refuse = null
  said.success = []
  orders.length = 0
})

describe('a leg’s own sheet', () => {
  it('keeps Buy and Sell shut until the lot size is read, then opens the ticket with the lot it read', async () => {
    show()
    const sheet = screen.getByRole('dialog', { name: 'NIFTY 25900 PE' })
    const buy = within(sheet).getByRole('button', { name: 'BUY' })
    const sell = within(sheet).getByRole('button', { name: 'SELL' })
    expect(buy).toBeDisabled()
    expect(sell).toBeDisabled()
    expect(sheet).toHaveTextContent('Reading the lot size.')
    fireEvent.click(buy)
    expect(orders).toEqual([])
    expect(lot.asked).toEqual([PUT])

    // The chain's own figure says 65; the ticket takes what the sheet read, not that.
    await act(async () => lot.answer?.({ lotSize: 75, tickSize: 0.05 }))
    await waitFor(() => expect(buy).toBeEnabled())
    expect(sell).toBeEnabled()
    fireEvent.click(sell)
    expect(orders).toHaveLength(1)
    expect(orders[0]).toMatchObject({
      action: 'SELL',
      item: { symbol: PUT, exchange: 'NFO' },
      contract: { lotSize: 75, tickSize: 0.05 },
    })
  })

  it('adds the leg to a watchlist through the watchlists’ own add, and says where it went', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))
    const sheet = await screen.findByRole('dialog', { name: 'Add to watchlist' })
    expect(sheet).toHaveTextContent('NIFTY 25900 PE')
    const row = await within(sheet).findByRole('button', { name: /Swing/ })
    expect(row).toHaveTextContent('1 of 250')
    fireEvent.click(row)
    await waitFor(() => expect(lists.added).toEqual([[1, PUT, 'NFO']]))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(said.success).toEqual(['Added NIFTY 25900 PE to Swing'])
  })

  it('does not offer a list that holds the leg already, or one that is full', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))
    const sheet = await screen.findByRole('dialog', { name: 'Add to watchlist' })
    const held = await within(sheet).findByRole('button', { name: /Hedges/ })
    expect(held).toBeDisabled()
    expect(held).toHaveTextContent('Already in this list')
    const full = within(sheet).getByRole('button', { name: /Crowded/ })
    expect(full).toBeDisabled()
    expect(full).toHaveTextContent('Full: 250 of 250')
    fireEvent.click(held)
    fireEvent.click(full)
    expect(lists.added).toEqual([])
  })

  it('shows the server’s sentence when the add is refused, and stays open to try another list', async () => {
    lists.refuse = 'That symbol is not in the master contract.'
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }))
    const sheet = await screen.findByRole('dialog', { name: 'Add to watchlist' })
    fireEvent.click(await within(sheet).findByRole('button', { name: /Swing/ }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent(
      'That symbol is not in the master contract.'
    )
    expect(within(sheet).getByRole('button', { name: /Swing/ })).toBeEnabled()
    expect(said.success).toEqual([])
  })
})
