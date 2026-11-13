/**
 * F11, the page: rows that follow the price. Real `LiveList`, `useLiveRows` and resolver over the market-data
 * test harness and the mock bridge; every `resolve` is counted.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return {
    webClient: bridge.current.client,
    apiClient: {
      post: async () => ({
        data: { status: 'success', data: { lotsize: 65, tick_size: 0.05 } },
      }),
    },
  }
})
vi.mock('@/api/trading', () => ({
  tradingApi: {
    getMultiQuotes: async (_key: string, symbols: Array<{ symbol: string; exchange: string }>) => ({
      status: 'success',
      results: symbols.map((each) => ({
        symbol: each.symbol,
        exchange: each.exchange,
        data: {
          ltp: 110,
          prev_close: 100,
          open: 100,
          high: 0,
          low: 0,
          volume: 0,
          oi: 0,
          bid: 0,
          ask: 0,
        },
      })),
    }),
  },
}))
vi.mock('@/api/oi-profile', () => ({
  oiProfileApi: { getUnderlyings: async () => ({ status: 'success', underlyings: ['NIFTY'] }) },
}))

import { createQueryClient } from '@/app/providers'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { FollowRowSheet } from './FollowRowSheet'
import { followPlan } from './follow'
import { answer, contract, futureAnswer, rule } from './followFixtures'
import type { FollowForm } from './followRows'
import { LiveList } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import type { FollowRow, Resolved, View } from './v2/types'

const T0 = Date.UTC(2026, 10, 13, 4, 42) // 10:12 IST
const b = () => bridge.current as NonNullable<typeof bridge.current>
const resolves = () => b().calls.filter((call) => call.op === 'resolve')

/** What each row's `resolve` answers now, by id. */
const answers = new Map<number, () => Resolved>()
const NIFTY = ['NSE_INDEX', 'NIFTY'] as const
const tick = (price: number) =>
  act(() => {
    feed.push(NIFTY[1], NIFTY[0], { ltp: price })
    frames.flush()
  })
const settle = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
    frames.flush()
  })
}

beforeEach(() => {
  feed.reset()
  frames.install()
  fakeClock()
  vi.setSystemTime(T0)
  b().reset()
  answers.clear()
  b().on('resolve', (call) => ({
    rows: (call.body as { rows: Array<{ id: number }> }).rows.map((each) =>
      (answers.get(each.id) ?? (() => answer(25900, { id: each.id })))()
    ),
  }))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

interface Props {
  rules: FollowRow[]
  openRowId?: number | null
  onForm?: (form: FollowForm) => void
  onOrder?: (order: { item: { symbol: string } }) => void
  v2?: boolean
}
function Page({
  rules,
  openRowId = null,
  onForm = () => {},
  onOrder = () => {},
  v2 = true,
}: Props) {
  return (
    <LiveList
      items={[]}
      apiKey="k"
      isOpen={() => true}
      loading={false}
      editing={false}
      listName="Swing"
      openItemId={null}
      panel={{ 'aria-label': 'Watchlist' }}
      onOpen={() => {}}
      onMove={() => {}}
      onRemove={() => {}}
      onCloseSheet={() => {}}
      onOrder={onOrder as never}
      onAlert={() => {}}
      flagNames={v2 ? [] : undefined}
      followRows={{ rules, openRowId, onOpenRow: () => {}, onForm }}
    />
  )
}
function mount(props: Props) {
  const client = createQueryClient()
  const tree = (p: Props) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Page {...p} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const out = render(tree(props))
  return { ...out, again: (p: Props) => out.rerender(tree(p)) }
}

describe('a row that follows the price', () => {
  it('shows its title, its chip and its detail, and the contract live from the feed', async () => {
    mount({ rules: [rule({ offset: 2 })] })
    await settle()
    const row = document.querySelector('[data-follow-row="1"]') as HTMLElement
    expect(within(row).getByText('NIFTY 25,900 CE')).toBeInTheDocument()
    expect(within(row).getByText('ATM+2')).toBeInTheDocument()
    expect(within(row).getByText('NFO · 24 Nov 2026 · follows NIFTY')).toBeInTheDocument()
    act(() => {
      feed.push('NIFTY24NOV2625900CE', 'NFO', { ltp: 133.5, prev_close: 100 })
      frames.flush()
    })
    expect(within(row).getByText('133.50')).toBeInTheDocument()
  })

  it('names the ATM chip with its help text, and a future row with its own chip', async () => {
    answers.set(2, () => futureAnswer(2))
    mount({
      rules: [rule(), rule({ id: 2, kind: 'month_future', strike_rule: null, opt_right: null })],
    })
    await settle()
    expect(
      screen.getByText(/^Nearest strike, kept while the price stays inside its band/)
    ).toBeInTheDocument()
    const row = document.querySelector('[data-follow-row="2"]') as HTMLElement
    expect(within(row).getByText('NIFTY FUT')).toBeInTheDocument()
    expect(within(row).getByText('Current-month FUT')).toBeInTheDocument()
  })

  it('follows the underlying once, however many rows stand on it', async () => {
    mount({ rules: [rule(), rule({ id: 2, opt_right: 'PE', offset: 1 })] })
    await settle()
    expect(feed.live().filter((key) => key.startsWith('NSE_INDEX:NIFTY'))).toHaveLength(1)
    // And in the cap's count: a repeated underlying takes one place.
    const plan = followPlan({ underlyings: ['NSE_INDEX:NIFTY', 'NSE_INDEX:NIFTY'], rows: [] })
    expect(plan.keys.size).toBe(1)
  })
})

describe('when a row asks the server again', () => {
  const band = { low: 25870, high: 25930 }
  beforeEach(() => answers.set(1, () => answer(25900, { band })))

  it('asks once for the first read and nothing for ticks inside the band', async () => {
    mount({ rules: [rule()] })
    await settle()
    tick(25905)
    tick(25915)
    await settle()
    expect(resolves()).toHaveLength(1)
  })

  it('asks once for ten ticks past the edge inside a second', async () => {
    mount({ rules: [rule()] })
    await settle()
    for (let i = 0; i < 10; i++) {
      tick(25940 + i)
      await settle(50)
    }
    expect(resolves()).toHaveLength(2)
    expect((resolves()[1].body as { rows: unknown[] }).rows[0]).toMatchObject({
      price: 25940,
      source: 'tick',
      held: 25900,
    })
  })

  it('asks once at valid_until, and nothing in 30 minutes without a reason', async () => {
    answers.set(1, () => answer(25900, { band, valid_until: T0 + 10 * 60_000 }))
    mount({ rules: [rule()] })
    await settle()
    expect(resolves()).toHaveLength(1)
    await settle(10 * 60_000 - 1000)
    expect(resolves()).toHaveLength(1)
    answers.set(1, () => answer(25900, { band, valid_until: null }))
    await settle(1000)
    expect(resolves()).toHaveLength(2)
    expect((resolves()[1].body as { rows: Array<{ held: unknown }> }).rows[0].held).toBeNull()
    await settle(30 * 60_000)
    expect(resolves()).toHaveLength(2)
  })

  it('asks once after a reconnect and once on returning to the page', async () => {
    mount({ rules: [rule()] })
    await settle()
    act(() => feed.setState({ connectionEpoch: 2 }))
    await settle()
    expect(resolves()).toHaveLength(2)
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await settle()
    expect(resolves()).toHaveLength(3)
  })

  it('says it is waiting, and asks on the first tick after', async () => {
    const words = "Waiting for NIFTY's price to pick the strike"
    answers.set(1, () =>
      answer(25900, { state: 'waiting', ce: null, strike: null, band: null, words })
    )
    mount({ rules: [rule()] })
    await settle()
    expect(screen.getByText(words)).toBeInTheDocument()
    answers.set(1, () => answer(25900, { band }))
    tick(25912)
    await settle()
    expect(resolves()).toHaveLength(2)
    expect(screen.queryByText(words)).toBeNull()
  })

  it('shows where the strike moved from for 60 seconds', async () => {
    mount({ rules: [rule()] })
    await settle()
    answers.set(1, () => answer(25950, { band: { low: 25920, high: 25980 }, price: 25931 }))
    tick(25931)
    await settle()
    expect(screen.getByText('Moved from 25,900 CE at 10:12 (NIFTY 25,931)')).toBeInTheDocument()
    expect(screen.getByText('NIFTY 25,950 CE')).toBeInTheDocument()
    await settle(59_000)
    expect(screen.queryByText(/^Moved from/)).not.toBeNull()
    await settle(2_000)
    expect(screen.queryByText(/^Moved from/)).toBeNull()
  })

  it("shows the server's words for a roll as sent, and for a contract it cannot find", async () => {
    answers.set(1, () =>
      answer(25900, { ce: contract(25900, '2026-12-01'), words: 'Rolled to 1 Dec 2026 at 15:30' })
    )
    mount({ rules: [rule(), rule({ id: 2, offset: 9 })] })
    await settle()
    expect(screen.getByText('Rolled to 1 Dec 2026 at 15:30')).toBeInTheDocument()
  })
})

describe("a row's sheet", () => {
  async function open() {
    const order = vi.fn()
    const form = vi.fn()
    answers.set(1, () => answer(25900, { band: { low: 25870, high: 25930 } }))
    const out = mount({ rules: [rule()], openRowId: 1, onOrder: order, onForm: form })
    await settle()
    return { ...out, order, form }
  }

  it('stays on the contract it opened with when the row moves, and Buy uses it', async () => {
    const { order } = await open()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('NIFTY 25,900 CE')).toBeInTheDocument()
    answers.set(1, () => answer(25950, { band: { low: 25920, high: 25980 } }))
    tick(25960)
    await settle()
    expect(within(dialog).getByText('NIFTY 25,900 CE')).toBeInTheDocument()
    expect(within(dialog).getByText('This row now follows 25,950 CE')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'BUY' }))
    expect(order).toHaveBeenCalledTimes(1)
    expect(order.mock.calls[0][0].item.symbol).toBe('NIFTY24NOV2625900CE')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show' }))
    await settle()
    expect(within(screen.getByRole('dialog')).getByText('NIFTY 25,950 CE')).toBeInTheDocument()
  })

  it('asks first before removing: Remove row opens the confirmation and sends nothing', async () => {
    const { form } = await open()
    fireEvent.click(screen.getByRole('button', { name: 'Remove row' }))
    expect(form).toHaveBeenCalledWith({ mode: 'remove', rowId: 1 })
    expect(b().calls.filter((call) => call.op === 'row_remove')).toHaveLength(0)
  })
})

describe('the symbol sheet of an underlying', () => {
  it('offers Add ATM row and Add current-month FUT row, and opens the form for each', async () => {
    const form = vi.fn()
    const client = createQueryClient()
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <LiveList
            items={[{ id: 7, symbol: 'NIFTY', exchange: 'NSE_INDEX', position: 0 }]}
            apiKey="k"
            isOpen={() => true}
            loading={false}
            editing={false}
            listName="Swing"
            openItemId={7}
            panel={{ 'aria-label': 'Watchlist' }}
            onOpen={() => {}}
            onMove={() => {}}
            onRemove={() => {}}
            onCloseSheet={() => {}}
            onOrder={() => {}}
            onAlert={() => {}}
            flagNames={[]}
            symbolActions={{ onCopy: () => {} }}
            followRows={{ rules: [], openRowId: null, onOpenRow: () => {}, onForm: form }}
          />
        </MemoryRouter>
      </QueryClientProvider>
    )
    await settle()
    await settle()
    fireEvent.click(await screen.findByRole('button', { name: 'Add ATM row' }))
    expect(form).toHaveBeenLastCalledWith({
      mode: 'add',
      kind: 'atm_option',
      underlying: 'NIFTY',
      exchange: 'NSE_INDEX',
      sectionId: null,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add current-month FUT row' }))
    expect(form).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'month_future' }))
  })
})

describe('the form', () => {
  const view = (follow: FollowRow[] = []): View =>
    ({
      ref: 'list:3',
      kind: 'list',
      name: 'Swing',
      version: '41',
      follow,
      rows: [],
      sections: [],
    }) as unknown as View
  const sheet = (form: FollowForm, rows: FollowRow[] = []) => {
    const done = vi.fn()
    const client = createQueryClient()
    render(
      <QueryClientProvider client={client}>
        <FollowRowSheet form={form} view={view(rows)} onClose={done} onSwap={() => {}} />
      </QueryClientProvider>
    )
    return done
  }
  const adding = (kind: 'atm_option' | 'month_future'): FollowForm => ({
    mode: 'add',
    kind,
    underlying: 'NIFTY',
    exchange: 'NSE_INDEX',
    sectionId: null,
  })
  beforeEach(() => b().on('row_add', () => ({ version: '42', id: 9, words: null })))

  it('sends the body of an ATM CE row and shows the preview', async () => {
    const done = sheet(adding('atm_option'))
    expect(screen.getByText(/^NIFTY ATM CE, nearest expiry\./)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Strike'), { target: { value: '2' } })
    expect(screen.getByText(/^NIFTY ATM\+2 CE, nearest expiry\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    fireEvent.click(screen.getByRole('button', { name: /Adding|Add row/ }))
    await settle()
    const calls = b().calls.filter((call) => call.op === 'row_add')
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toEqual({
      list: 3,
      version: '41',
      kind: 'atm_option',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'nearest',
      strike_rule: 'atm',
      offset: 2,
      opt_right: 'CE',
    })
    expect(done).toHaveBeenCalled()
  })

  it('sends the body of a PE row on next month, and of a FUT row with no strike or side', async () => {
    sheet(adding('atm_option'))
    fireEvent.click(screen.getByLabelText("Next month's"))
    fireEvent.click(screen.getByLabelText('PE'))
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    await settle()
    expect(b().calls.at(-1)?.body).toMatchObject({
      expiry_rule: 'next_month',
      opt_right: 'PE',
      offset: 0,
    })
  })

  it('sends a FUT row without a strike, and shows its preview', async () => {
    sheet(adding('month_future'))
    expect(screen.getByText("NIFTY future, this month's expiry.")).toBeInTheDocument()
    expect(screen.queryByLabelText('Strike')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    await settle()
    expect(b().calls.at(-1)?.body).toEqual({
      list: 3,
      version: '41',
      kind: 'month_future',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'month',
    })
  })

  it('removes after asking, and sends once however many times it is tapped', async () => {
    b().on('row_remove', () => ({ version: '42', id: 1, words: null }))
    const done = sheet({ mode: 'remove', rowId: 1 }, [rule()])
    expect(screen.getByText(/stops following NIFTY and leaves Swing/)).toBeInTheDocument()
    expect(b().calls.filter((call) => call.op === 'row_remove')).toHaveLength(0)
    const remove = screen.getByRole('button', { name: 'Remove' })
    fireEvent.click(remove)
    fireEvent.click(remove)
    fireEvent.click(remove)
    await settle()
    const calls = b().calls.filter((call) => call.op === 'row_remove')
    expect(calls).toHaveLength(1)
    expect(calls[0].params).toMatchObject({ version: '41' })
    expect(done).toHaveBeenCalledTimes(1)
  })

  // Review M6: Remove takes the place of Change in the same open sheet, where "Remove this row" was
  // tapped; the second tap of a double tap must not land on the confirmation's Remove.
  it('ignores taps on the remove confirmation for half a second after it takes the form’s place', async () => {
    b().on('row_remove', () => ({ version: '42', id: 1, words: null }))
    OPEN_GUARD.ms = 500
    function Swapping() {
      const [form, setForm] = useState<FollowForm>({ mode: 'edit', rowId: 1 })
      return (
        <FollowRowSheet form={form} view={view([rule()])} onClose={() => {}} onSwap={setForm} />
      )
    }
    render(
      <QueryClientProvider client={createQueryClient()}>
        <Swapping />
      </QueryClientProvider>
    )
    await settle(600) // past the sheet's own half second
    fireEvent.click(screen.getByRole('button', { name: 'Remove this row' }))
    const remove = await screen.findByRole('button', { name: 'Remove' })
    fireEvent.click(remove) // the tail of a double tap
    await settle()
    expect(b().calls.filter((call) => call.op === 'row_remove')).toHaveLength(0)
    await settle(550)
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await settle()
    expect(b().calls.filter((call) => call.op === 'row_remove')).toHaveLength(1)
  })

  it("says the server's words when it refuses, and sends again only on a new tap", async () => {
    b().on('row_add', () => ({
      status: 'error',
      code: 'limit',
      message: 'A list can have 20 rows that follow the price.',
    }))
    sheet(adding('atm_option'))
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    await settle()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'A list can have 20 rows that follow the price.'
    )
  })
})

describe('with the switch off', () => {
  it("draws no follow row and asks nothing, as today's page", async () => {
    mount({ rules: [rule()], v2: false })
    await settle()
    expect(document.querySelector('[data-follow-row]')).toBeNull()
    expect(resolves()).toHaveLength(0)
    expect(feed.live()).toHaveLength(0)
  })
})
