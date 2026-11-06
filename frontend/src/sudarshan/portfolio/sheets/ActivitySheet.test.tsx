/**
 * The Add sheet in jsdom: kinds as chips, the asset search in its order, the dry run 300 ms after
 * typing stops with its words, one save with the tab version and a request id that a resend
 * reuses, and Edit and Duplicate from the list filled in.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
}))
vi.mock('@/sudarshan/access', () => ({
  usePriceAccess: () => ({ state: 'ready', apiKey: 'key' }),
}))
const search = vi.hoisted(() => ({ run: vi.fn() }))
vi.mock('@/sudarshan/watchlist/api', () => ({ searchSymbols: search.run }))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { row, view } from '../tab/fixtures'
import { type Account, type Asset, WORDS } from '../types'
import { ActivitySheet, type AddMode, DRY_RUN_MS } from './ActivitySheet'
import { entry, tab } from './fixtures'

const GOLD = tab(2, 'Gold', { version: 4 })
const ACCOUNT = {
  id: 1,
  member_id: 1,
  name: 'Demat',
  kind: 'demat',
  broker: null,
  note: null,
  system: false,
  version: 1,
} as Account
const INFY = {
  kind: 'listed',
  id: 7,
  name: 'Infosys Ltd',
  exchange: 'NSE',
  symbol: 'INFY',
  asset_class: 'equity',
  isin: null,
  version: 1,
} as Asset

function serve(extra: Record<string, (args: any) => unknown> = {}) {
  api.call.mockImplementation(async (op: string, args: any) => {
    if (op === 'tab_get')
      return {
        enabled: true,
        data: view([row(7, 'Infosys Ltd', { symbol: 'INFY' })], { assets: [INFY] }),
      }
    if (op in extra) {
      const out = extra[op](args)
      if (out instanceof Error) throw out
      return { enabled: true, data: out }
    }
    throw new Error(`unexpected op ${op}`)
  })
}

const SAVED = {
  activity: entry(9),
  effect: { words: 'Cost ₹14,302.50 with ₹20.00 charges', cost_paise: 1, gain_paise: null },
  tab_version: 5,
}

function show(
  mode: AddMode = { kind: 'add' },
  accounts: Account[] = [ACCOUNT],
  seed?: (q: ReturnType<typeof createQueryClient>) => void
) {
  const handlers = { onClose: vi.fn(), onSaved: vi.fn(), onAccountCreated: vi.fn() }
  const client = createQueryClient()
  seed?.(client)
  render(
    <QueryClientProvider client={client}>
      <ActivitySheet
        open
        tab={GOLD}
        accounts={accounts}
        members={[{ id: 1, name: 'Me', relation: null, version: 1 } as any]}
        mode={mode}
        {...handlers}
      />
    </QueryClientProvider>
  )
  return handlers
}

async function chooseInfy() {
  await userEvent.type(screen.getByPlaceholderText(/Share, fund/), 'INFY')
  await userEvent.click(await screen.findByRole('button', { name: /INFY/ }))
}

beforeEach(() => {
  api.call.mockReset()
  search.run.mockReset()
  search.run.mockResolvedValue([
    { symbol: 'INFY', exchange: 'NSE', name: 'Infosys Ltd' },
    { symbol: 'INFY', exchange: 'BFO', name: 'derivative' },
  ])
  window.localStorage.clear()
})

describe('the Add sheet', () => {
  it('buys: finds the share in the list, works the amount, checks it after 300 ms and saves once', async () => {
    serve({
      mf_search: () => ({ items: [], note: null }),
      activity_create: (a) => (a.dry_run ? { ...SAVED, activity: null } : SAVED),
    })
    const { onSaved } = show()
    await chooseInfy()
    await screen.findByText('Infosys Ltd') // the tab's own asset was reused: no asset_create
    expect(api.call.mock.calls.some((c) => c[0] === 'asset_create')).toBe(false)
    expect((screen.getByLabelText(/^Price/) as HTMLInputElement).value).toBe('100') // the tab's last price
    await userEvent.type(screen.getByLabelText('Quantity'), '10')
    expect((screen.getByLabelText(/^Amount of the trade/) as HTMLInputElement).value).toBe('1000')
    const calls = () => api.call.mock.calls.filter((c) => c[0] === 'activity_create')
    expect(calls()).toHaveLength(0) // not yet: typing just stopped
    expect(
      await screen.findByText('Cost ₹14,302.50 with ₹20.00 charges', {}, { timeout: 2000 })
    ).toBeTruthy()
    expect(calls()).toHaveLength(1)
    expect(calls()[0][1]).toMatchObject({
      id: 2,
      version: 4,
      dry_run: true,
      entry: { kind: 'BUY', asset_id: 7, account_id: 1, qty_u: 10_000_000, amount_paise: 100_000 },
    })
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    await userEvent.click(save)
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const sent = calls().filter((c) => !c[1].dry_run)
    expect(sent).toHaveLength(1)
    expect(sent[0][1].request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(sent[0][1]).toMatchObject({ id: 2, version: 4 })
    expect(window.localStorage.getItem('sudarshan.portfolio.last-account')).toBe('1')
  })

  it('searches shares first (cash only), then funds, then the tab’s own assets', async () => {
    serve({
      mf_search: () => ({
        items: [
          {
            amfi_code: '100',
            name: 'Index Fund',
            asset_class: 'mf_equity',
            category: null,
            amc: null,
            nav_u: 123_4500,
            nav_day: 20261103,
          },
        ],
        note: null,
      }),
    })
    show()
    await userEvent.type(screen.getByPlaceholderText(/Share, fund/), 'IN')
    await screen.findByRole('button', { name: /Index Fund/ })
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(headings.slice(0, 2)).toEqual(['Shares and ETFs', 'Mutual funds'])
    expect(screen.queryByText('derivative')).toBeNull() // BFO is not cash
    expect(screen.getByRole('button', { name: /New manual asset/ })).toBeTruthy()
  })

  it('says funds are not ready while the service answers not_ready', async () => {
    api.call.mockImplementation(async (op: string) => {
      if (op === 'tab_get') return { enabled: true, data: view([]) }
      throw new PortfolioError({ code: 'not_ready', message: WORDS.not_ready, entryId: null })
    })
    show()
    await userEvent.type(screen.getByPlaceholderText(/Share, fund/), 'IN')
    expect(await screen.findByText('Mutual fund search is not ready yet')).toBeTruthy()
  })

  it('says shares are whole numbers and does not check the entry', async () => {
    serve({ mf_search: () => ({ items: [], note: null }), activity_create: () => SAVED })
    show()
    await chooseInfy()
    await userEvent.type(screen.getByLabelText('Quantity'), '10.5')
    expect(await screen.findByText('Shares are whole numbers.')).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, DRY_RUN_MS + 200))
    expect(api.call.mock.calls.some((c) => c[0] === 'activity_create')).toBe(false)
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('a date after today can be saved as a draft only', async () => {
    serve({ mf_search: () => ({ items: [], note: null }), activity_create: () => SAVED })
    show()
    await chooseInfy()
    await userEvent.type(screen.getByLabelText('Quantity'), '1')
    const date = screen.getByLabelText('Date') as HTMLInputElement
    await userEvent.clear(date)
    await userEvent.type(date, '2099-01-01')
    expect(await screen.findByText(WORDS.future_date)).toBeTruthy()
    await userEvent.click(await screen.findByRole('button', { name: 'Save as draft' }))
    await waitFor(() =>
      expect(api.call.mock.calls.some((c) => c[0] === 'activity_create' && !c[1].dry_run)).toBe(
        true
      )
    )
    const sent = api.call.mock.calls.find((c) => c[0] === 'activity_create' && !c[1].dry_run)
    expect(sent?.[1].entry.status).toBe('draft')
  })

  it('shows a refusal from the check in words and does not allow Save', async () => {
    const words = 'You hold 5 of these on that day; this sells 10.'
    serve({
      mf_search: () => ({ items: [], note: null }),
      activity_create: () => new PortfolioError({ code: 'refused', message: words, entryId: null }),
    })
    show()
    await chooseInfy()
    await userEvent.click(screen.getByRole('button', { name: 'Sell' }))
    await userEvent.type(screen.getByLabelText('Quantity'), '10')
    expect(await screen.findByText(words, {}, { timeout: 2000 })).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('a lost answer says Not confirmed and a resend reuses the request id', async () => {
    let tries = 0
    serve({
      mf_search: () => ({ items: [], note: null }),
      activity_create: (a) => {
        if (a.dry_run) return { ...SAVED, activity: null }
        tries += 1
        return tries === 1
          ? new PortfolioError({
              code: 'unanswered',
              message: 'No answer came from OpenAlgo.',
              entryId: null,
            })
          : SAVED
      },
    })
    const { onSaved } = show()
    await chooseInfy()
    await userEvent.type(screen.getByLabelText('Quantity'), '1')
    await screen.findByText('Cost ₹14,302.50 with ₹20.00 charges', {}, { timeout: 2000 })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.not_confirmed)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const sent = api.call.mock.calls.filter((c) => c[0] === 'activity_create' && !c[1].dry_run)
    expect(sent).toHaveLength(2)
    expect(sent[1][1].request_id).toBe(sent[0][1].request_id)
  })

  it('shows the version conflict sentence as the service wrote it', async () => {
    serve({
      mf_search: () => ({ items: [], note: null }),
      activity_create: (a) =>
        a.dry_run
          ? { ...SAVED, activity: null }
          : new PortfolioError({
              code: 'version_conflict',
              message: WORDS.version_conflict,
              entryId: null,
            }),
    })
    show()
    await chooseInfy()
    await userEvent.type(screen.getByLabelText('Quantity'), '1')
    await screen.findByText('Cost ₹14,302.50 with ₹20.00 charges', {}, { timeout: 2000 })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect((await screen.findByRole('alert')).textContent).toBe(WORDS.version_conflict)
  })

  it('More holds the other kinds, each with its own fields', async () => {
    serve()
    show()
    await userEvent.click(screen.getByRole('button', { name: /More/ }))
    const more = screen.getByRole('group', { name: 'More kinds' })
    expect(
      within(more)
        .getAllByRole('button')
        .map((b) => b.textContent)
    ).toEqual([
      'Fee',
      'Interest',
      'Transfer',
      'Opening holding',
      'Split',
      'Bonus',
      'Rights',
      'Add a valuable',
      'Loan',
    ])
    await userEvent.click(within(more).getByRole('button', { name: 'Split' }))
    expect(screen.getByLabelText('New shares')).toBeTruthy()
    expect(screen.queryByLabelText('Quantity')).toBeNull()
    await userEvent.click(within(more).getByRole('button', { name: 'Transfer' }))
    expect(screen.getByRole('button', { name: 'From outside' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'To outside' })).toBeTruthy()
    await userEvent.click(within(more).getByRole('button', { name: 'Loan' }))
    expect(screen.getByLabelText('Name of the loan')).toBeTruthy()
  })

  it('selects the last account used, and offers a new one', async () => {
    serve()
    window.localStorage.setItem('sudarshan.portfolio.last-account', '2')
    const second = { ...ACCOUNT, id: 2, name: 'Bank' }
    show({ kind: 'add' }, [ACCOUNT, second])
    expect(screen.getByRole('button', { name: 'Bank' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Demat' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: /New account/ })).toBeTruthy()
  })

  it('breaks charges down into parts', async () => {
    serve()
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Break the charges down' }))
    await userEvent.type(screen.getByLabelText('Brokerage'), '20')
    await userEvent.type(screen.getByLabelText('STT'), '14.30')
    expect(screen.getByText('Charges: ₹34.3')).toBeTruthy()
  })
})

describe('Edit and Duplicate', () => {
  const seeded = (q: ReturnType<typeof createQueryClient>) =>
    q.setQueryData([...PORTFOLIO_KEY, 'activities', 2], {
      pages: [
        {
          items: [
            entry(5, { asset_id: 7, qty_u: 10_000_000, price_u: 1_000_000, amount_paise: 100_000 }),
          ],
          next: null,
        },
      ],
      pageParams: [undefined],
    })

  it('Edit opens filled in and sends the whole entry to the entry’s own route', async () => {
    serve({ activity_update: (a) => (a.dry_run ? { ...SAVED, activity: null } : SAVED) })
    show({ kind: 'edit', activityId: 5 }, [ACCOUNT], seeded)
    const qty = (await screen.findByLabelText('Quantity')) as HTMLInputElement
    expect(qty.value).toBe('10')
    expect(screen.getByText('Edit entry')).toBeTruthy()
    await userEvent.clear(qty)
    await userEvent.type(qty, '12')
    await screen.findByText('Cost ₹14,302.50 with ₹20.00 charges', {}, { timeout: 2000 })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.call.mock.calls.some((c) => c[0] === 'activity_update' && !c[1].dry_run)).toBe(
        true
      )
    )
    const sent = api.call.mock.calls.find((c) => c[0] === 'activity_update' && !c[1].dry_run)
    expect(sent?.[1]).toMatchObject({ id: 5, version: 4, entry: { qty_u: 12_000_000 } })
  })

  const draftSeeded = (q: ReturnType<typeof createQueryClient>) =>
    q.setQueryData([...PORTFOLIO_KEY, 'activities', 2], {
      pages: [
        {
          items: [
            entry(5, {
              status: 'draft',
              asset_id: 7,
              qty_u: 10_000_000,
              price_u: 1_000_000,
              amount_paise: 100_000,
            }),
          ],
          next: null,
        },
      ],
      pageParams: [undefined],
    })
  const updates = () =>
    api.call.mock.calls.filter((c) => c[0] === 'activity_update' && !c[1].dry_run)

  it('an old draft stays a draft when saved, and says what each button does', async () => {
    serve({ activity_update: (a) => (a.dry_run ? { ...SAVED, activity: null } : SAVED) })
    show({ kind: 'edit', activityId: 5 }, [ACCOUNT], draftSeeded)
    await screen.findByLabelText('Quantity')
    expect(
      screen.getByText(/"Save as draft" keeps it out\. "Save and count it" adds it/)
    ).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Save as draft' }))
    await waitFor(() => expect(updates()).toHaveLength(1))
    expect(updates()[0][1].entry.status).toBe('draft')
  })

  it('"Save and count it" posts the draft', async () => {
    serve({ activity_update: (a) => (a.dry_run ? { ...SAVED, activity: null } : SAVED) })
    show({ kind: 'edit', activityId: 5 }, [ACCOUNT], draftSeeded)
    await screen.findByLabelText('Quantity')
    await userEvent.click(screen.getByRole('button', { name: 'Save and count it' }))
    await waitFor(() => expect(updates()).toHaveLength(1))
    expect(updates()[0][1].entry.status).toBe('posted')
  })

  it('Duplicate opens filled in, dated today, and creates a new entry', async () => {
    serve({ activity_create: (a) => (a.dry_run ? { ...SAVED, activity: null } : SAVED) })
    show({ kind: 'duplicate', activityId: 5 }, [ACCOUNT], seeded)
    const qty = (await screen.findByLabelText('Quantity')) as HTMLInputElement
    expect(qty.value).toBe('10')
    expect((screen.getByLabelText('Date') as HTMLInputElement).value).not.toBe('2026-11-04')
    await screen.findByText('Cost ₹14,302.50 with ₹20.00 charges', {}, { timeout: 2000 })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.call.mock.calls.some((c) => c[0] === 'activity_create' && !c[1].dry_run)).toBe(
        true
      )
    )
  })

  it('says so when the entry is not in the list any more', async () => {
    serve()
    show({ kind: 'edit', activityId: 99 }, [ACCOUNT])
    expect(await screen.findByText(/not open any more/)).toBeTruthy()
  })
})
