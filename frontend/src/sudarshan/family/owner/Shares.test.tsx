import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ shareList: vi.fn(), shareSet: vi.fn(), shareRemove: vi.fn() }))
vi.mock('../ownerApi', async () => ({
  ...(await vi.importActual<typeof import('../ownerApi')>('../ownerApi')),
  ownerApi: api,
}))
const lists = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/api/watchlist', () => ({ watchlistApi: lists }))
const portfolio = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', () => ({ portfolioCall: portfolio.call }))

import { OwnerError } from '../ownerApi'
import { cardOf, shareOf } from './fixtures'
import { Shares, TABS_NOTE } from './Shares'

const members = [
  cardOf(),
  cardOf({
    id: 2,
    display_name: 'Test Two',
    role: 'child',
    privacy: { pnl: false, qty: false, funds: false, orders: false },
  }),
]
const watchlists = [
  {
    id: 7,
    name: 'Morning list',
    position: 0,
    items: [{ id: 1, symbol: 'AAA', exchange: 'NSE', position: 0 }],
  },
  { id: 8, name: 'Long term', position: 1, items: [] },
]

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <Shares members={members} />
    </QueryClientProvider>
  )
  return { spy }
}

beforeEach(() => {
  for (const fn of [...Object.values(api), lists.list, portfolio.call]) fn.mockReset()
  api.shareList.mockResolvedValue([])
  lists.list.mockResolvedValue(watchlists)
  portfolio.call.mockResolvedValue({ enabled: true, data: { tabs: [] } })
})

describe('sharing watchlists', () => {
  it('lists the owner lists, none shared yet, for every member first', async () => {
    mount()
    const morning = await screen.findByRole('switch', { name: 'Share Morning list' })
    expect(morning).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByLabelText('Share with', { selector: '#share-lists-target' })).toHaveValue(
      'all'
    )
    expect(screen.getByText('1 symbol · Not shared')).toBeInTheDocument()
    expect(screen.getByText('0 symbols · Not shared')).toBeInTheDocument()
  })

  it('shares a list with every member: no member and no role', async () => {
    api.shareSet.mockResolvedValue(shareOf())
    mount()
    await userEvent.click(await screen.findByRole('switch', { name: 'Share Morning list' }))
    expect(api.shareSet).toHaveBeenCalledWith({
      kind: 'watchlist',
      ref: '7',
      member_id: null,
      role: null,
      level: 'view',
      expires_at: null,
    })
    expect(await screen.findByText('Morning list is shared.')).toBeInTheDocument()
  })

  it('shares a list with one member', async () => {
    api.shareSet.mockResolvedValue(shareOf())
    mount()
    await userEvent.selectOptions(
      await screen.findByLabelText('Share with', { selector: '#share-lists-target' }),
      '2'
    )
    await userEvent.click(screen.getByRole('switch', { name: 'Share Long term' }))
    expect(api.shareSet).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'watchlist', ref: '8', member_id: 2 })
    )
  })

  it('stops sharing with the row id and its own version', async () => {
    api.shareList.mockResolvedValue([shareOf({ id: 11, ref: '7', member_id: null, version: 4 })])
    api.shareRemove.mockResolvedValue({ ok: true, words: null })
    mount()
    const morning = await screen.findByRole('switch', { name: 'Share Morning list' })
    await waitFor(() => expect(morning).toHaveAttribute('aria-checked', 'true'))
    await userEvent.click(morning)
    expect(api.shareRemove).toHaveBeenCalledWith({ id: 11, version: 4 })
    expect(await screen.findByText('Morning list is no longer shared.')).toBeInTheDocument()
  })

  it('shows a list shared with every member as shared for one member, and leaves that switch alone', async () => {
    api.shareList.mockResolvedValue([shareOf({ id: 11, ref: '7', member_id: null })])
    mount()
    await userEvent.selectOptions(
      await screen.findByLabelText('Share with', { selector: '#share-lists-target' }),
      '1'
    )
    const morning = screen.getByRole('switch', { name: 'Share Morning list' })
    await waitFor(() => expect(morning).toHaveAttribute('aria-checked', 'true'))
    expect(morning).toBeDisabled()
    expect(screen.getByText('Shared with every member')).toBeInTheDocument()
  })

  it('sends once when a switch is tapped twice, and holds the others while it is on its way', async () => {
    let finish!: (share: unknown) => void
    api.shareSet.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    mount()
    const morning = await screen.findByRole('switch', { name: 'Share Morning list' })
    await userEvent.dblClick(morning)
    expect(api.shareSet).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('switch', { name: 'Share Long term' })).toBeDisabled()
    await act(async () => finish(shareOf()))
  })

  it('sends once for two taps in the same frame, before the switches are disabled', async () => {
    let finish!: (share: unknown) => void
    api.shareSet.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    mount()
    const morning = await screen.findByRole('switch', { name: 'Share Morning list' })
    act(() => {
      morning.click()
      morning.click()
    })
    expect(api.shareSet).toHaveBeenCalledTimes(1)
    await act(async () => finish(shareOf()))
  })

  it('says not confirmed for a write that was not answered, and reads the shares again', async () => {
    api.shareSet.mockRejectedValue(
      new OwnerError(
        'not_confirmed',
        'Not confirmed: open the Family page again to see whether it was done.'
      )
    )
    const { spy } = mount()
    await userEvent.click(await screen.findByRole('switch', { name: 'Share Morning list' }))
    const said = await screen.findByText(/^Not confirmed/)
    expect(said.textContent).not.toMatch(/fail/i)
    expect(spy).toHaveBeenCalled()
  })

  it('says so when the lists cannot be read', async () => {
    lists.list.mockRejectedValue(new Error('x'))
    mount()
    expect(await screen.findByText(/watchlists could not be read/)).toBeInTheDocument()
  })
})

describe('sharing books', () => {
  it('shows the five books of a member, and shares one for that member', async () => {
    api.shareSet.mockResolvedValue(shareOf({ kind: 'book', ref: 'orders' }))
    mount()
    const books = (await screen.findByRole('heading', { name: 'Books' })).closest(
      'section'
    ) as HTMLElement
    for (const label of ['Positions', 'Holdings', 'Orders', 'Trades', 'Funds']) {
      expect(within(books).getByRole('switch', { name: `Share ${label}` })).toBeInTheDocument()
    }
    await userEvent.click(within(books).getByRole('switch', { name: 'Share Orders' }))
    expect(api.shareSet).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'book', ref: 'orders', member_id: 1, role: null })
    )
  })

  it('says beside a book what the member privacy hides, with an icon and the words', async () => {
    mount()
    const books = (await screen.findByRole('heading', { name: 'Books' })).closest(
      'section'
    ) as HTMLElement
    // The first member shows orders, hides funds.
    expect(within(books).getAllByText('Hidden by their privacy settings')).toHaveLength(1)
    await userEvent.selectOptions(within(books).getByLabelText('Share with'), '2')
    // The second member hides orders, trades and funds.
    expect(within(books).getAllByText('Hidden by their privacy settings')).toHaveLength(3)
  })

  it('stops sharing a book with the row version', async () => {
    api.shareList.mockResolvedValue([
      shareOf({ id: 5, kind: 'book', ref: 'holdings', member_id: 1, version: 2 }),
    ])
    api.shareRemove.mockResolvedValue({ ok: true, words: null })
    mount()
    const books = (await screen.findByRole('heading', { name: 'Books' })).closest(
      'section'
    ) as HTMLElement
    const holdings = within(books).getByRole('switch', { name: 'Share Holdings' })
    await waitFor(() => expect(holdings).toHaveAttribute('aria-checked', 'true'))
    await userEvent.click(holdings)
    expect(api.shareRemove).toHaveBeenCalledWith({ id: 5, version: 2 })
  })
})

describe('portfolio tabs', () => {
  it('are read only: the tabs by name and the sentence that they cannot be shared from here', async () => {
    portfolio.call.mockResolvedValue({
      enabled: true,
      data: {
        tabs: [
          { id: 1, name: 'Upstox', archived: false },
          { id: 2, name: 'Old tab', archived: true },
          { id: 3, name: 'Gold', archived: false },
        ],
      },
    })
    mount()
    expect(await screen.findByText(TABS_NOTE)).toBeInTheDocument()
    expect(await screen.findByText('Gold')).toBeInTheDocument()
    expect(screen.queryByText('Old tab')).toBeNull()
    const section = screen
      .getByRole('heading', { name: 'Portfolio tabs' })
      .closest('section') as HTMLElement
    expect(within(section).queryByRole('switch')).toBeNull()
    expect(within(section).queryByRole('combobox')).toBeNull()
    expect(portfolio.call).toHaveBeenCalledWith('state', undefined, expect.anything())
  })
})
