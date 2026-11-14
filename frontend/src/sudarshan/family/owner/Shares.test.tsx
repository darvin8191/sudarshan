import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, configure, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The first render of this file pays for loading three sections and their queries; on a shared
// two-core box under load that has taken past the default second, so the waits get four.
configure({ asyncUtilTimeout: 4000 })

const api = vi.hoisted(() => ({ shareList: vi.fn(), shareSet: vi.fn(), shareRemove: vi.fn() }))
vi.mock('../ownerApi', async () => ({
  ...(await vi.importActual<typeof import('../ownerApi')>('../ownerApi')),
  ownerApi: api,
}))
const lists = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/api/watchlist', () => ({ watchlistApi: lists }))
const portfolio = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('@/sudarshan/portfolio/api', async () => ({
  ...(await vi.importActual<typeof import('@/sudarshan/portfolio/api')>(
    '@/sudarshan/portfolio/api'
  )),
  portfolioCall: portfolio.call,
}))

import { PortfolioError } from '@/sudarshan/portfolio/api'
import type { TabShare } from '@/sudarshan/portfolio/types'
import { OwnerError } from '../ownerApi'
import { cardOf, NOW, shareOf } from './fixtures'
import { END_AFTER_NOW, ENDED_BLOCKS, TABS_NOTE, UPSTOX_NOTE } from './PortfolioTabShares'
import { BOOKS_RULE, Shares } from './Shares'

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

function mount(onOpenMember?: (id: number) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const spy = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <Shares members={members} now={NOW} onOpenMember={onOpenMember} />
    </QueryClientProvider>
  )
  return { spy }
}

beforeEach(() => {
  for (const fn of [...Object.values(api), lists.list, portfolio.call]) fn.mockReset()
  api.shareList.mockResolvedValue([])
  lists.list.mockResolvedValue(watchlists)
  portfolio.call.mockImplementation(portfolioAnswers({ tabs: [], shares: [] }))
})

const TABS = [
  { id: 1, name: 'Upstox', archived: false, system: true },
  { id: 2, name: 'Old tab', archived: true, system: false },
  { id: 3, name: 'Gold', archived: false, system: false },
]
const floor = (member: string, role: string) => ({
  member,
  role,
  shown: { pnl: true, qty: true, funds: false, orders: true },
  version: 1,
})

/** The portfolio bridge, by op: the tabs, the shares and floors, and each write's answer. */
function portfolioAnswers({
  tabs = TABS,
  shares = [] as TabShare[],
  privacy = [floor('1', 'viewer'), floor('2', 'child')],
}: {
  tabs?: unknown[]
  shares?: TabShare[]
  privacy?: unknown[]
}) {
  return async (name: string, args: Record<string, unknown>) => {
    if (name === 'state') return { enabled: true, data: { tabs } }
    if (name === 'shares_list') return { enabled: true, data: { shares, privacy } }
    if (name === 'share_set') {
      return { enabled: true, data: { ...args, version: ((args.version as number) ?? 0) + 1 } }
    }
    return { enabled: true, data: { removed: true } }
  }
}

const tabShare = (over: Partial<TabShare>): TabShare => ({
  tab_id: 3,
  viewer: '1',
  level: 'view',
  expires_at: null,
  version: 1,
  ...over,
})
const writes = (op: string) =>
  portfolio.call.mock.calls.filter(([name]) => name === op).map(([, args]) => args)

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

// Review H1 (v0.12.0): the server never read a share row of kind `book`, so a switch here said
// "Not shared" above a book the member was reading. Each of these fails on the old section.
describe('books', () => {
  const section = async () =>
    (await screen.findByRole('heading', { name: 'Books' })).closest('section') as HTMLElement

  it('has no switch and never says Shared or Not shared beside a book', async () => {
    portfolio.call.mockImplementation(
      portfolioAnswers({ shares: [tabShare({ tab_id: 1, viewer: 'role:viewer' })] })
    )
    mount()
    const books = await section()
    expect(within(books).queryAllByRole('switch')).toHaveLength(0)
    expect(within(books).queryByText('Not shared')).toBeNull()
    expect(within(books).queryByText('Shared')).toBeNull()
    expect(within(books).queryByLabelText('Share with')).toBeNull()
    expect(within(books).getByText(BOOKS_RULE)).toBeInTheDocument()
  })

  it('never writes a share of kind book, whatever is tapped in the section', async () => {
    const opened: number[] = []
    mount((id) => opened.push(id))
    const books = await section()
    for (const button of within(books).getAllByRole('button')) await userEvent.click(button)
    expect(api.shareSet).not.toHaveBeenCalled()
    expect(api.shareRemove).not.toHaveBeenCalled()
  })

  it('says what each member Orders and Funds switches stand at, and opens their sheet', async () => {
    const opened: number[] = []
    mount((id) => opened.push(id))
    const books = await section()
    // The first member shows orders and hides funds; the second hides both.
    const first = within(books).getByRole('button', { name: /Privacy settings of Test One/ })
    expect(first.textContent).toContain('Orders and trades shown · Funds hidden')
    const second = within(books).getByRole('button', { name: /Privacy settings of Test Two/ })
    expect(second.textContent).toContain('Orders and trades hidden · Funds hidden')
    await userEvent.click(second)
    expect(opened).toEqual([2])
  })

  it('leads to the Portfolio tabs section, where the Upstox tab is shared', async () => {
    mount()
    const books = await section()
    const tabs = await screen.findByRole('heading', { name: 'Portfolio tabs' })
    await userEvent.click(within(books).getByRole('button', { name: /Upstox tab share/ }))
    expect(tabs).toHaveFocus()
  })

  it('leaves a book row an earlier release made out of the page', async () => {
    api.shareList.mockResolvedValue([shareOf({ id: 5, kind: 'book', ref: 'holdings' })])
    mount()
    const books = await section()
    expect(within(books).queryByText(/Holdings/)).toBeNull()
  })
})

describe('portfolio tabs', () => {
  const section = async () =>
    (await screen.findByRole('heading', { name: 'Portfolio tabs' })).closest(
      'section'
    ) as HTMLElement

  it('lists the tabs that are not archived, what each is shared with, and the Upstox tab note', async () => {
    portfolio.call.mockImplementation(portfolioAnswers({}))
    mount()
    const tabs = await section()
    expect(await within(tabs).findByText('Gold')).toBeInTheDocument()
    expect(within(tabs).queryByText('Old tab')).toBeNull()
    expect(within(tabs).getByText(TABS_NOTE)).toBeInTheDocument()
    expect(within(tabs).getByText(UPSTOX_NOTE)).toBeInTheDocument()
    expect(within(tabs).getAllByText('Not shared')).toHaveLength(2)
    expect(portfolio.call).toHaveBeenCalledWith('state', undefined, expect.anything())
    expect(portfolio.call).toHaveBeenCalledWith('shares_list', {}, expect.anything())
  })

  it('shares a tab with every member: a share for each role, new, with no version', async () => {
    portfolio.call.mockImplementation(portfolioAnswers({}))
    mount()
    const tabs = await section()
    await userEvent.click(await within(tabs).findByRole('button', { name: 'Share Gold' }))
    const form = within(tabs).getByRole('form', { name: 'Share Gold' })
    expect(within(form).getByLabelText('Share with')).toHaveValue('all')
    await userEvent.click(within(form).getByRole('button', { name: 'Share' }))
    expect(await screen.findByText('Gold is shared with every member: View.')).toBeInTheDocument()
    expect(writes('share_set')).toEqual([
      { tab_id: 3, viewer: 'role:viewer', level: 'view', expires_at: null, version: null },
      { tab_id: 3, viewer: 'role:child', level: 'view', expires_at: null, version: null },
    ])
  })

  it('shares a tab with one member, Restricted, until a date in India time', async () => {
    portfolio.call.mockImplementation(portfolioAnswers({}))
    mount()
    const tabs = await section()
    await userEvent.click(await within(tabs).findByRole('button', { name: 'Share Gold' }))
    const form = within(tabs).getByRole('form', { name: 'Share Gold' })
    await userEvent.selectOptions(within(form).getByLabelText('Share with'), '2')
    await userEvent.click(within(form).getByRole('radio', { name: /Restricted/ }))
    await userEvent.click(within(form).getByRole('radio', { name: /On a date and time/ }))
    const end = within(form).getByLabelText('Share ends at')
    await userEvent.clear(end)
    await userEvent.type(end, '2026-11-20T18:00')
    await userEvent.click(within(form).getByRole('button', { name: 'Share' }))
    await waitFor(() =>
      expect(writes('share_set')).toEqual([
        {
          tab_id: 3,
          viewer: '2',
          level: 'restricted',
          expires_at: Date.UTC(2026, 10, 20, 12, 30),
          version: null,
        },
      ])
    )
  })

  it('refuses an end that has passed, in words, and sends nothing', async () => {
    portfolio.call.mockImplementation(portfolioAnswers({}))
    mount()
    const tabs = await section()
    await userEvent.click(await within(tabs).findByRole('button', { name: 'Share Gold' }))
    const form = within(tabs).getByRole('form', { name: 'Share Gold' })
    await userEvent.click(within(form).getByRole('radio', { name: /On a date and time/ }))
    const end = within(form).getByLabelText('Share ends at')
    await userEvent.clear(end)
    await userEvent.type(end, '2020-01-01T10:00')
    await userEvent.click(within(form).getByRole('button', { name: 'Share' }))
    expect(await screen.findByText(END_AFTER_NOW)).toBeInTheDocument()
    expect(writes('share_set')).toEqual([])
  })

  it('changes a share with the version the portfolio holds', async () => {
    portfolio.call.mockImplementation(
      portfolioAnswers({ shares: [tabShare({ viewer: '1', version: 4 })] })
    )
    mount()
    const tabs = await section()
    await userEvent.click(
      await within(tabs).findByRole('button', { name: 'Change how Gold is shared with Test One' })
    )
    const form = within(tabs).getByRole('form', { name: 'Share Gold' })
    expect(within(form).queryByLabelText('Share with')).toBeNull()
    await userEvent.click(within(form).getByRole('radio', { name: /Restricted/ }))
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(writes('share_set')).toEqual([
        { tab_id: 3, viewer: '1', level: 'restricted', expires_at: null, version: 4 },
      ])
    )
  })

  it('stops a share with every member, each role at its own version', async () => {
    portfolio.call.mockImplementation(
      portfolioAnswers({
        shares: [
          tabShare({ viewer: 'role:viewer', version: 2 }),
          tabShare({ viewer: 'role:child', version: 5 }),
        ],
      })
    )
    mount()
    const tabs = await section()
    await userEvent.click(
      await within(tabs).findByRole('button', { name: 'Stop sharing Gold with Every member' })
    )
    expect(
      await screen.findByText('Gold is no longer shared with every member.')
    ).toBeInTheDocument()
    expect(writes('share_remove')).toEqual([
      { tab_id: 3, viewer: 'role:viewer', version: 2 },
      { tab_id: 3, viewer: 'role:child', version: 5 },
    ])
  })

  it('says which part of every member took effect when the second share is refused', async () => {
    const answers = portfolioAnswers({})
    portfolio.call.mockImplementation(async (name: string, args: Record<string, unknown>) => {
      if (name === 'share_set' && args.viewer === 'role:child') {
        throw new PortfolioError({
          code: 'unavailable',
          message: 'The portfolio service is not answering.',
          entryId: null,
        })
      }
      return answers(name, args)
    })
    const { spy } = mount()
    const tabs = await section()
    await userEvent.click(await within(tabs).findByRole('button', { name: 'Share Gold' }))
    await userEvent.click(within(tabs).getByRole('button', { name: 'Share' }))
    expect(
      await screen.findByText(
        'Gold is now shared with members with the Viewer role only. The portfolio service is not answering.'
      )
    ).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith({
      queryKey: ['sudarshan', 'family-owner', 'portfolio-shares'],
    })
  })

  it('a share that was not answered is not confirmed, never failed', async () => {
    const answers = portfolioAnswers({})
    portfolio.call.mockImplementation(async (name: string, args: Record<string, unknown>) => {
      if (name === 'share_set') {
        throw new PortfolioError({ code: 'not_confirmed', message: 'x', entryId: null })
      }
      return answers(name, args)
    })
    mount()
    const tabs = await section()
    await userEvent.click(await within(tabs).findByRole('button', { name: 'Share Gold' }))
    await userEvent.click(within(tabs).getByRole('button', { name: 'Share' }))
    const said = await screen.findByText(/^Not confirmed/)
    expect(said.textContent).not.toMatch(/fail/i)
    expect(writes('share_set')).toHaveLength(1) // the second role is not sent after it
  })

  it('sends a stop once when it is tapped twice in the same frame', async () => {
    let finish!: (value: unknown) => void
    const answers = portfolioAnswers({ shares: [tabShare({ viewer: '1' })] })
    portfolio.call.mockImplementation((name: string, args: Record<string, unknown>) =>
      name === 'share_remove'
        ? new Promise((resolve) => {
            finish = resolve
          })
        : answers(name, args)
    )
    mount()
    const tabs = await section()
    const stop = await within(tabs).findByRole('button', {
      name: 'Stop sharing Gold with Test One',
    })
    act(() => {
      stop.click()
      stop.click()
    })
    expect(writes('share_remove')).toHaveLength(1)
    await act(async () => finish({ enabled: true, data: { removed: true } }))
  })

  it('says an ended share of a member keeps the share with every member from them', async () => {
    portfolio.call.mockImplementation(
      portfolioAnswers({
        shares: [
          tabShare({ viewer: 'role:viewer' }),
          tabShare({ viewer: 'role:child' }),
          tabShare({ viewer: '1', expires_at: NOW - 60_000 }),
        ],
      })
    )
    mount()
    const tabs = await section()
    expect(await within(tabs).findByText(ENDED_BLOCKS)).toBeInTheDocument()
    expect(within(tabs).getByText(/^Ended /)).toBeInTheDocument()
  })

  it('names a member whose privacy has not reached the portfolio, so every member misses them', async () => {
    portfolio.call.mockImplementation(
      portfolioAnswers({
        shares: [tabShare({ viewer: 'role:viewer' }), tabShare({ viewer: 'role:child' })],
        privacy: [floor('1', 'viewer')],
      })
    )
    mount()
    expect(
      await screen.findByText(/Tabs shared with every member do not reach Test Two yet/)
    ).toBeInTheDocument()
  })
})
