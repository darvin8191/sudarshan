/**
 * F12, the page: strike rows (CE, strike, PE). Real `LiveList`, `useLiveRows` and resolver over the market-data
 * test harness and the mock bridge; every `resolve` is counted.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
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
import { pairAnswer, pairRule } from './followFixtures'
import type { FollowForm } from './followRows'
import { LiveList } from './LiveList'
import type { FollowRow, Resolved, View } from './v2/types'

const T0 = Date.UTC(2026, 10, 13, 4, 42) // 10:12 IST
const b = () => bridge.current as NonNullable<typeof bridge.current>

/** What each row's `resolve` answers now, by id. */
const answers = new Map<number, () => Resolved>()
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

const CE = 'NIFTY24NOV2625900CE'
const PE = 'NIFTY24NOV2625900PE'
const leg = (row: HTMLElement, side: 'CE' | 'PE') =>
  row.querySelector(`[data-leg="${side}"]`) as HTMLElement
const setup = () => answers.set(1, () => pairAnswer(25900))

describe('a strike row', () => {
  beforeEach(setup)

  it('is one row of three blocks: CE, the strike and expiry with the chip, PE', async () => {
    mount({ rules: [pairRule()] })
    await settle()
    const row = document.querySelector('[data-strike-row="1"]') as HTMLElement
    expect(within(row).getByText('25,900 \u00b7 24 Nov')).toBeInTheDocument()
    expect(within(row).getByText('ATM')).toBeInTheDocument()
    expect(leg(row, 'CE')).toBeInTheDocument()
    expect(leg(row, 'PE')).toBeInTheDocument()
  })

  it('shows each leg with its own tick arrow and signed change', async () => {
    mount({ rules: [pairRule()] })
    await settle()
    act(() => {
      feed.push(CE, 'NFO', { ltp: 133.5, prev_close: 120 })
      feed.push(PE, 'NFO', { ltp: 90, prev_close: 100 })
      frames.flush()
    })
    act(() => {
      feed.push(CE, 'NFO', { ltp: 134.5, prev_close: 120 })
      feed.push(PE, 'NFO', { ltp: 89, prev_close: 100 })
      frames.flush()
    })
    const row = document.querySelector('[data-strike-row="1"]') as HTMLElement
    const ce = leg(row, 'CE')
    const pe = leg(row, 'PE')
    expect(within(ce).getByText('134.50')).toBeInTheDocument()
    expect(ce.querySelector('[data-tick="up"]')).not.toBeNull()
    expect(ce.textContent).toContain('+14.50')
    expect(within(pe).getByText('89.00')).toBeInTheDocument()
    expect(pe.querySelector('[data-tick="down"]')).not.toBeNull()
    expect(pe.textContent).toContain('\u221211.00')
  })

  it('follows both legs in Quote mode and the underlying in LTP mode', async () => {
    mount({ rules: [pairRule()] })
    await settle()
    expect(feed.live()).toEqual(['NSE_INDEX:NIFTY:LTP', `NFO:${CE}:Quote`, `NFO:${PE}:Quote`])
  })

  it('counts 2 in the cap, whole or not at all', () => {
    const plan = followPlan({
      rows: [{ key: 'NSE:A' }, { key: 'NFO:C', keys: ['NFO:C', 'NFO:P'] }, { key: 'NSE:B' }],
    })
    expect(plan.keys.size).toBe(4)
    const full = Array.from({ length: 249 }, (_, index) => ({ key: `NSE:S${index}` }))
    const cut = followPlan({ rows: [...full, { key: 'NFO:C', keys: ['NFO:C', 'NFO:P'] }] })
    expect(cut.keys.size).toBe(249)
    expect(cut.keys.has('NFO:C')).toBe(false)
  })

  it("says the server's words in the row when a side is not in the contract list", async () => {
    answers.set(1, () => ({
      ...pairAnswer(25900),
      state: 'missing',
      ce: null,
      pe: null,
      words: "NIFTY 25,900 PE is not in today's contract list.",
    }))
    mount({ rules: [pairRule()] })
    await settle()
    const row = document.querySelector('[data-strike-row]') as HTMLElement
    expect(row).toHaveTextContent("NIFTY 25,900 PE is not in today's contract list.")
    expect(feed.live().some((key) => key.includes('NIFTY24NOV'))).toBe(false)
  })

  it('draws nothing of it with the switch off', async () => {
    mount({ rules: [pairRule()], v2: false })
    await settle()
    expect(document.querySelector('[data-strike-row]')).toBeNull()
    expect(feed.live()).toHaveLength(0)
  })
})

describe('the form, with Both as one row', () => {
  const view = (follow: FollowRow[]) =>
    ({
      ref: 'list:3',
      kind: 'list',
      name: 'Swing',
      version: '41',
      follow,
      rows: [],
      sections: [],
    }) as unknown as View
  const adding = (over: Partial<Extract<FollowForm, { mode: 'add' }>> = {}): FollowForm => ({
    mode: 'add',
    kind: 'atm_option',
    underlying: 'NIFTY',
    exchange: 'NSE_INDEX',
    sectionId: null,
    ...over,
  })
  const sheet = (form: FollowForm) => {
    render(
      <QueryClientProvider client={createQueryClient()}>
        <FollowRowSheet form={form} view={view([])} onClose={() => {}} onSwap={() => {}} />
      </QueryClientProvider>
    )
  }
  beforeEach(() => b().on('row_add', () => ({ version: '42', id: 9, words: null })))

  it('sends a strike pair on the ATM rule with the offset and no side', async () => {
    sheet(adding())
    fireEvent.change(screen.getByLabelText('Strike'), { target: { value: '1' } })
    fireEvent.click(screen.getByLabelText('Both, as one row'))
    expect(
      screen.getByText(/^NIFTY ATM\+1 CE and PE as one row, nearest expiry\./)
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    await settle()
    const call = b()
      .calls.filter((each) => each.op === 'row_add')
      .at(-1)
    expect(call?.body).toEqual({
      list: 3,
      version: '41',
      kind: 'strike_pair',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'nearest',
      strike_rule: 'atm',
      offset: 1,
    })
  })

  it("keeps a picked strike fixed (the seam of F14's picker), and says when its expiry moves", async () => {
    const expiries = [
      { date: '2026-11-24', label: 'Nearest 24 Nov', month: true },
      { date: '2026-12-01', label: '1 Dec', month: false },
      { date: '2026-12-08', label: '8 Dec', month: false },
    ]
    sheet(adding({ kind: 'strike_pair', fixed: { strike: 25900, expiry: 'next', expiries } }))
    expect(screen.queryByLabelText('Strike')).toBeNull()
    // Review M3: the strike is fixed, the expiry is not, and the form says the date and the move.
    expect(
      screen.getByText('Strike 25,900, fixed: it does not move with the price.')
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'NIFTY 25,900 CE and PE as one row, next expiry (1 Dec today). When 24 Nov expires, this row moves to 8 Dec.'
      )
    ).toBeInTheDocument()
    expect(screen.getByRole('dialog').textContent).not.toMatch(/for good|stays where it is/)
    fireEvent.click(screen.getByRole('button', { name: 'Add row' }))
    await settle()
    expect(
      b()
        .calls.filter((each) => each.op === 'row_add')
        .at(-1)?.body
    ).toEqual({
      list: 3,
      version: '41',
      kind: 'strike_pair',
      underlying: 'NIFTY',
      underlying_exchange: 'NSE_INDEX',
      expiry_rule: 'next',
      strike_rule: 'fixed',
      strike: 25900,
    })
  })

  it('says how a fixed strike moves when the dates are not known, and never that it is kept for good', () => {
    sheet(adding({ kind: 'strike_pair', fixed: { strike: 25900, expiry: 'month' } }))
    expect(
      screen.getByText(
        "NIFTY 25,900 CE and PE as one row, this month's expiry. When it expires, this row moves to next month's expiry."
      )
    ).toBeInTheDocument()
    expect(screen.getByRole('dialog').textContent).not.toMatch(/for good|stays where it is/)
  })
})
