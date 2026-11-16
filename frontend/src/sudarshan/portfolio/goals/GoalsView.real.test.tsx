/**
 * The Goals view with the real `TabValue` over the `liveTab` fakes (review B1). A goal that counts
 * a tab once made the view and `TabValue` redraw each other without end: `useTabLive` builds its
 * feed line anew on every render, the effect reported that new object, and the view stored it as a
 * change. The production build spent 9 of every 10 seconds in script, and a tap then crashed the
 * app (React error 185). Nothing here is a fake of the pair under test: only the feed, the
 * calendar and the service are.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  lines: new Map<string, unknown>(),
  connected: true,
  renders: 0,
  list: vi.fn(),
  call: vi.fn(),
}))
vi.mock('@/sudarshan/live/useFrameLines', () => ({
  useFrameLines: () => {
    mocks.renders += 1 // one call for each render of a TabValue
    // An endless update never lets a timer run, so the test's own clock cannot end it: stop here.
    if (mocks.renders > 500) throw new Error('TabValue drew more than 500 times: an endless update')
    return {
      lines: mocks.lines,
      directions: new Map(),
      feed: { connected: mocks.connected, fallback: false, epoch: 1 },
    }
  },
}))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listGoals: mocks.list,
  portfolioCall: mocks.call,
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { createQueryClient } from '@/app/providers'
import type { Goal, TabInfo } from '../types'
import { GoalsView } from './GoalsView'

const TABS = [
  { id: 4, name: 'Long term', version: 1 },
  { id: 5, name: 'Gold', version: 1 },
] as TabInfo[]
const READ_AT = 1_000
const line = (qty: number, cost: number) => ({
  account_id: 1,
  qty_u: qty,
  open_qty_u: qty,
  cost_paise: cost,
  in_today_paise: 0,
  out_today_paise: 0,
  over_year_qty_u: 0,
})
const FIXED = {
  realised_paise: 0,
  income_paise: 0,
  fees_paise: 0,
  money_in_paise: 0,
  money_out_paise: 0,
  closed_today_paise: 0,
  closed_today_base_paise: 0,
  liabilities_paise: 0,
}
/** Tab 4: 100 shares at ₹1,250. Tab 5: gold typed at ₹1,00,000, which no tick moves. */
const VIEWS: Record<number, object> = {
  4: {
    read_at: READ_AT,
    fixed: FIXED,
    rows: [
      {
        asset: { kind: 'listed', exchange: 'NSE', symbol: 'INFY' },
        today_mode: 'ok',
        price: { price_u: 12_500_000, prev_close_u: 12_500_000 },
        lines: [line(100_000_000, 1_00_000_00)],
      },
    ],
  },
  5: {
    read_at: READ_AT,
    fixed: FIXED,
    rows: [
      {
        asset: { kind: 'manual', exchange: null, symbol: null },
        today_mode: 'ok',
        price: { price_u: 1_000_000_000, prev_close_u: 1_000_000_000 },
        lines: [line(1_000_000, 90_000_00)],
      },
    ],
  },
}
const goal = (more: Partial<Goal> = {}): Goal => ({
  id: 1,
  member_id: 1,
  name: 'House',
  name_alt: null,
  target_paise: 10_00_000_00,
  target_day: 20310331,
  rate_bp: 1200,
  tab_ids: [4, 5],
  fixed_paise: null,
  archived: false,
  version: 2,
  value_now_paise: 2_25_000_00,
  progress_pct: '22.5000',
  required_monthly_paise: 9_000_00,
  months_left: 52,
  on_track: false,
  gone_tab_ids: [],
  problem: null,
  ...more,
})
const tick = (ltp: number, at: number) => ({
  symbol: 'INFY',
  exchange: 'NSE',
  data: { ltp, close: 1250 },
  lastUpdate: at,
  updateSource: 'websocket',
  connectionEpoch: 1,
})

function show() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <GoalsView tabs={TABS} />
    </QueryClientProvider>
  )
}
const idle = (ms: number) => act(() => new Promise<void>((done) => setTimeout(done, ms)))

beforeEach(() => {
  mocks.lines = new Map()
  mocks.connected = true
  mocks.renders = 0
  mocks.list
    .mockReset()
    .mockResolvedValue({ enabled: true, data: { as_of: READ_AT, goals: [goal()] } })
  mocks.call.mockReset().mockImplementation(async (name: string, args: { id: number }) => {
    if (name !== 'tab_get') throw new Error(`unexpected call ${name}`)
    return { enabled: true, data: VIEWS[args.id] }
  })
})

describe('the Goals view with the real TabValue', () => {
  it('left alone, a goal that counts two tabs draws a bounded number of times, and its sheets open', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(await card.findByText('₹2,25,000.00')).toBeTruthy() // both tabs have reported
    await idle(300)
    mocks.renders = 0
    await idle(2_200)
    // Two TabValues, each drawn by the one-second clock of its feed line and nothing else. The
    // endless update drew them thousands of times in the same window.
    expect(mocks.renders).toBeLessThanOrEqual(12)
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/Maximum update depth/)
    // The tap that crashed the production build (React error 185).
    await userEvent.click(screen.getByRole('button', { name: 'Change House' }))
    const sheet = within(await screen.findByRole('dialog'))
    expect(sheet.getByRole('button', { name: 'Archive this goal' })).toBeTruthy()
    expect((sheet.getByLabelText('Long term') as HTMLInputElement).checked).toBe(true)
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Goal' }))
    expect(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Add goal' }))
    errors.mockRestore()
  }, 20_000)

  it('moves with a tick, says Real-time though the gold tab cannot tick, and reports each change once', async () => {
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(await card.findByText('₹2,25,000.00')).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull() // no tick yet
    mocks.lines = new Map([['NSE:INFY', tick(1300, Date.now())]])
    await idle(1_200) // the clock redraws the tab, which reads the new tick
    expect(await card.findByText('₹2,30,000.00')).toBeTruthy()
    expect(card.getByText('23.0%')).toBeTruthy()
    expect(card.getByText('Real-time')).toBeTruthy() // review M1: the idle gold tab says nothing
    // The amount to add follows the value shown, not the service's last read (review L3).
    expect(card.queryByText(/₹9,000 a month/)).toBeNull()
    expect(card.getByText(/^Add about ₹[\d,]+ a month at 12% a year/)).toBeTruthy()
    mocks.renders = 0
    await idle(2_200)
    expect(mocks.renders).toBeLessThanOrEqual(12)
  }, 20_000)
})
