/**
 * F12, the page: the strike sheet. Real `LiveList`, `useLiveRows` and resolver over the market-data
 * test harness and the mock bridge; every `resolve` is counted.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/MarketDataManager', () => import('@/test/marketDataHarness'))
const searched = vi.hoisted(() => ({ rows: [] as unknown[], noPe: false }))
const bridge = vi.hoisted(() => ({
  current: null as null | ReturnType<typeof import('./v2/mockBridge').createBridge>,
}))
vi.mock('@/api/client', async () => {
  const { createBridge } = await import('./v2/mockBridge')
  bridge.current = createBridge()
  return {
    webClient: bridge.current.client,
    apiClient: {
      post: async (url: string) => ({
        data:
          url === '/search'
            ? { status: 'success', data: searched.rows }
            : { status: 'success', data: { lotsize: 65, tick_size: 0.05 } },
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
          ltp: searched.noPe && each.symbol.endsWith('PE') ? 0 : 110,
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
import { legGreeks } from '@/lib/optionGreeks'
import { fakeClock, frames } from '@/test/frames'
import { feed } from '@/test/marketDataHarness'
import { followPlan } from './follow'
import { pairAnswer, pairRule } from './followFixtures'
import type { FollowForm } from './followRows'
import { LiveList } from './LiveList'
import { OPEN_GUARD } from './openGuard'
import type { FollowRow, Resolved } from './v2/types'

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

const tick = (price: number) =>
  act(() => {
    feed.push('NIFTY', 'NSE_INDEX', { ltp: price })
    frames.flush()
  })
const sym = (day: string, strike: number, right: string) => `NIFTY${day}26${strike}${right}`
const hit = (day: string, iso: string, strike: number, right: string) => ({
  symbol: sym(day, strike, right),
  exchange: 'NFO',
  name: 'NIFTY',
  instrumenttype: right,
  lotsize: 65,
  tick_size: 0.05,
  expiry: iso,
  strike,
})
const EXPIRIES = [
  ['24NOV', '24-NOV-26'],
  ['01DEC', '01-DEC-26'],
  ['08DEC', '08-DEC-26'],
  ['15DEC', '15-DEC-26'],
  ['22DEC', '22-DEC-26'],
  ['29DEC', '29-DEC-26'],
]
const search = (strike: number) => {
  searched.rows = [
    ...EXPIRIES.flatMap(([day, iso]) => [hit(day, iso, strike, 'CE'), hit(day, iso, strike, 'PE')]),
    { ...hit('24NOV', '24-NOV-26', strike, 'CE'), name: 'BANKNIFTY' },
  ]
}
const push = (strike: number, ce: number, pe: number, extra = {}) =>
  act(() => {
    feed.push(sym('24NOV', strike, 'CE'), 'NFO', { ltp: ce, prev_close: ce - 5, ...extra })
    feed.push(sym('24NOV', strike, 'PE'), 'NFO', { ltp: pe, prev_close: pe + 5, ...extra })
    frames.flush()
  })
const sheet = () => document.querySelector('[data-strike-sheet]') as HTMLElement

beforeEach(() => {
  search(25900)
  searched.noPe = false
  answers.set(1, () => pairAnswer(25900))
  OPEN_GUARD.ms = 0
})

describe('the strike sheet', () => {
  it('shows both legs with OI, and the straddle', async () => {
    mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    push(25900, 200.3, 140.25, { oi: 123456 })
    const s = within(sheet())
    expect(s.getByText('Straddle 340.55')).toBeInTheDocument()
    expect(s.getAllByText('1,23,456', { selector: 'td' })).toHaveLength(2)
    expect(sheet().textContent).not.toMatch(/OI change/i)
    expect(s.getByText('Open option chain').closest('a')?.getAttribute('href')).toBe(
      '/optionchain?exchange=NFO&underlying=NIFTY'
    )
  })

  it('works IV and Greeks out as optionGreeks does, from the parity forward', async () => {
    answers.set(1, () => pairAnswer(24000))
    // 7 days before the 24 Nov 15:30 close: the library's own vector for an at-the-forward leg.
    vi.setSystemTime(Date.UTC(2026, 10, 17, 10, 0))
    mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    const price = 192.2580800966898
    push(24000, price, price)
    const t = 7 / 365
    const ce = legGreeks('c', 24000, 24000, t, 0, price)
    const pe = legGreeks('p', 24000, 24000, t, 0, price)
    expect(ce?.iv).toBeCloseTo(14.5, 6)
    const row = (label: string) =>
      Array.from(sheet().querySelectorAll('tr')).find(
        (tr) => tr.firstElementChild?.textContent === label
      )
    const cells = (label: string) =>
      Array.from(row(label)?.querySelectorAll('td') ?? []).map((td) => td.textContent)
    expect(cells('IV')).toEqual(['14.50%', '14.50%'])
    expect(cells('Delta')).toEqual([ce?.delta.toFixed(3), pe?.delta.toFixed(3)])
    expect(cells('Delta')).toEqual(['0.504', '-0.496'])
    expect(cells('Theta')).toEqual([ce?.theta.toFixed(2), pe?.theta.toFixed(2)])
    expect(cells('Theta')[0]).toBe('-13.73')
    expect(cells('Gamma')[0]).toBe(ce?.gamma.toFixed(5))
    expect(cells('Vega')[0]).toBe(ce?.vega.toFixed(2))
  })

  it('says why IV is not worked out while a side has no price', async () => {
    searched.noPe = true
    mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    act(() => {
      feed.push(sym('24NOV', 25900, 'CE'), 'NFO', { ltp: 200 })
      frames.flush()
    })
    expect(
      within(sheet()).getByText('IV not worked out: no price on one side yet.')
    ).toBeInTheDocument()
    expect(within(sheet()).getByText('Straddle \u2014')).toBeInTheDocument()
  })

  it('opens the existing ticket on the contract shown, behind the 500 ms guard', async () => {
    const order = vi.fn()
    OPEN_GUARD.ms = 500
    mount({ rules: [pairRule()], openRowId: 1, onOrder: order })
    await settle()
    const names = ['Buy CE', 'Sell CE', 'Buy PE', 'Sell PE']
    fireEvent.click(within(sheet()).getByRole('button', { name: 'Buy CE' }))
    expect(order).not.toHaveBeenCalled()
    await settle(500)
    const expected = [
      ['BUY', 'CE'],
      ['SELL', 'CE'],
      ['BUY', 'PE'],
      ['SELL', 'PE'],
    ]
    names.forEach((name, index) => {
      fireEvent.click(screen.getByRole('button', { name }))
      const [action, right] = expected[index]
      expect(order).toHaveBeenLastCalledWith(
        expect.objectContaining({
          action,
          item: expect.objectContaining({ symbol: sym('24NOV', 25900, right), exchange: 'NFO' }),
          contract: { lotSize: 65, tickSize: 0.05 },
        })
      )
    })
    expect(order).toHaveBeenCalledTimes(4)
  })

  it('follows the next four expiries while open, first in the cap, and releases them on close', async () => {
    const out = mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    const others = feed.live().filter((key) => /NIFTY(01|08|15|22)DEC/.test(key))
    expect(others).toHaveLength(8)
    expect(feed.live().some((key) => key.includes('29DEC'))).toBe(false)
    act(() => {
      feed.push(sym('01DEC', 25900, 'CE'), 'NFO', { ltp: 250, prev_close: 240 })
      frames.flush()
    })
    expect(
      within(sheet().querySelector('[data-other-expiry="2026-12-01"]') as HTMLElement).getByText(
        '250.00'
      )
    ).toBeInTheDocument()
    expect(sheet().textContent).toContain('1 Dec')
    out.again({ rules: [pairRule()], openRowId: null })
    await settle()
    expect(feed.live().filter((key) => key.includes('DEC'))).toEqual([])
    out.unmount()
    expect(feed.live()).toEqual([])
  })

  it('counts the sheet first: its ten instruments stay in when the rows would fill the cap', () => {
    const sheetKeys = Array.from({ length: 10 }, (_, index) => `NFO:S${index}`)
    const rows = Array.from({ length: 250 }, (_, index) => ({ key: `NSE:R${index}` }))
    const plan = followPlan({ sheet: sheetKeys, rows })
    expect(plan.keys.size).toBe(250)
    for (const key of sheetKeys) expect(plan.keys.has(key)).toBe(true)
    expect(plan.keys.has('NSE:R249')).toBe(false)
  })

  it('stays on the contracts it opened with when the row moves, and says so with Show', async () => {
    answers.set(1, () => pairAnswer(25900, { band: { low: 25870, high: 25930 } }))
    mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    expect(screen.getByRole('dialog')).toHaveTextContent('NIFTY 25,900')
    answers.set(1, () => pairAnswer(25950, { band: { low: 25920, high: 25980 } }))
    tick(25960)
    await settle(2500)
    expect(within(sheet()).getByText('This row now follows 25,950')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveTextContent('NIFTY 25,900')
    expect(sheet().querySelectorAll(`[data-leg]`)).toHaveLength(2)
    fireEvent.click(within(sheet()).getByRole('button', { name: 'Show' }))
    await settle()
    expect(screen.getByRole('dialog')).toHaveTextContent('NIFTY 25,950')
  })

  it("shows the server's words in the sheet when a side is missing", async () => {
    answers.set(1, () => ({
      ...pairAnswer(25900),
      state: 'missing',
      ce: null,
      pe: null,
      words: "NIFTY 25,900 PE is not in today's contract list.",
    }))
    mount({ rules: [pairRule()], openRowId: 1 })
    await settle()
    expect(
      within(sheet()).getByText("NIFTY 25,900 PE is not in today's contract list.", {
        selector: '[data-strike-words]',
      })
    ).toBeInTheDocument()
    expect(within(sheet()).queryByRole('button', { name: 'Buy CE' })).toBeNull()
  })

  it('opens no strike sheet with the switch off', async () => {
    mount({ rules: [pairRule()], openRowId: 1, v2: false })
    await settle()
    expect(sheet()).toBeNull()
  })
})
