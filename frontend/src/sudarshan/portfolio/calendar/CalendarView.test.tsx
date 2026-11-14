/**
 * The Calendar view and "To confirm" (P7 F4), against lane C's answer shapes: the service's
 * sentence on the owner's own quantity, the other way round, one tap for the Upstox account, the
 * ready entry first for a tab, the banner when no future event is listed, and the list by month.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ call: vi.fn() }))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  portfolioCall: api.call,
  getCalendar: () => api.call('calendar'),
  listCorporateActions: () => api.call('ca_list'),
  decideCorporateAction: (args: object) => api.call('ca_decide', args),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import type { CaEvent, CaExpected, CalendarReport, CalendarRow, TabInfo } from '../types'
import { CALENDAR_OFF, CalendarView, NO_FUTURE } from './CalendarView'
import { entriesFor, typedRatio, wayOf } from './confirm'
import { factText, holdText } from './format'

const TAB = { id: 4, name: 'Long term', version: 11 } as TabInfo
const UPSTOX: CaExpected = {
  scope: 'upstox',
  held_qty_u: 100_000_000,
  ratio_num: 1,
  ratio_den: 2,
  words: 'Bonus 1:2: you held 100, you get 50 more, 150 in all. Is that right?',
  added_qty_u: 50_000_000,
  after_qty_u: 150_000_000,
  other: {
    ratio_num: 2,
    ratio_den: 1,
    words: 'Bonus 2:1: you held 100, you get 200 more, 300 in all. Is that right?',
    added_qty_u: 200_000_000,
    after_qty_u: 300_000_000,
  },
}
const IN_TAB: CaExpected = {
  ...UPSTOX,
  scope: 'tab:4',
  tab_id: 4,
  name: 'Long term',
  entries: [
    { kind: 'BONUS', trade_date: 20260910, asset_id: 31, account_id: 2, qty_u: 50_000_000 },
  ],
}
const BONUS: CaEvent = {
  id: 21,
  isin: 'INE000A01010',
  name: 'Alpha Industries',
  kind: 'bonus',
  ex_day: 20260910,
  amount_u: null,
  ratio_num: 1,
  ratio_den: 2,
  details: [],
  read_at: 1,
  gone_at: null,
  decisions: [],
  expected: [UPSTOX, IN_TAB],
}
const row = (more: Partial<CalendarRow>): CalendarRow => ({
  event_id: 1,
  isin: 'INE000B01018',
  name: 'Beta Bank',
  kind: 'dividend',
  ex_day: 20261203,
  amount_u: 55_000,
  ratio_num: null,
  ratio_den: null,
  held_qty_u: 300_000_000,
  expected_paise: 1_65_000,
  checked_at: Date.UTC(2026, 10, 12, 10),
  ...more,
})
const CALENDAR: CalendarReport = {
  from: 20260816,
  to: 20270513,
  listed_future: true,
  calendar: [
    row({}),
    row({
      event_id: 2,
      ex_day: 20260910,
      kind: 'bonus',
      amount_u: null,
      ratio_num: 1,
      ratio_den: 2,
      expected_paise: null,
    }),
    row({
      event_id: 3,
      ex_day: 20261215,
      name: null,
      held_qty_u: null,
      expected_paise: null,
      checked_at: null,
    }),
  ],
}

function serve(over: Record<string, unknown> = {}) {
  const answers: Record<string, unknown> = {
    calendar: CALENDAR,
    ca_list: { events: [], to_confirm: [BONUS], reads: [] },
    ca_decide: { id: 1 },
    activity_create: {
      activity: null,
      effect: { words: 'Adds 50 shares at no cost.' },
      tab_version: 12,
    },
    ...over,
  }
  api.call.mockImplementation(async (op: string) => {
    const answer = answers[op]
    if (answer instanceof Error) throw answer
    return { enabled: true, data: answer }
  })
}
function show() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <CalendarView tabs={[TAB]} />
    </QueryClientProvider>
  )
}
const calls = (op: string) => api.call.mock.calls.filter((c) => c[0] === op).map((c) => c[1])

beforeEach(() => {
  api.call.mockReset()
  vi.useFakeTimers({ toFake: ['Date'] }).setSystemTime(new Date('2026-11-14T12:00:00+05:30'))
})

describe('the words', () => {
  it('the public fact, and what it means on what is held', () => {
    expect(factText(row({}))).toBe('₹5.50 a share')
    expect(factText(row({ amount_u: null, ratio_num: 1, ratio_den: 5 }))).toBe(
      'Ratio 1:5, as Upstox lists it'
    )
    expect(factText(row({ amount_u: null }))).toBe('Amount not given')
    expect(holdText(row({}), 20261114)).toBe('You hold 300: about ₹1,650.00')
    expect(holdText(row({ ex_day: 20260910, expected_paise: null }), 20261114)).toBe('You held 300')
    expect(holdText(row({ held_qty_u: null }), 20261114)).toBeNull()
  })

  it('the reading on the card and the entries that put it into a tab', () => {
    expect(wayOf(UPSTOX, false)?.ratio_den).toBe(2)
    expect(wayOf(UPSTOX, true)?.ratio_num).toBe(2)
    expect(wayOf({ ...UPSTOX, ratio_num: null, ratio_den: null }, false)).toBeNull()
    expect(entriesFor('bonus', IN_TAB, true)).toEqual([
      { ...IN_TAB.entries?.[0], qty_u: 200_000_000 },
    ])
    const split = {
      ...IN_TAB,
      entries: [
        {
          kind: 'SPLIT',
          trade_date: 1,
          asset_id: 31,
          account_id: null,
          ratio_num: 1,
          ratio_den: 2,
        },
      ],
    } as CaExpected
    expect(entriesFor('split', split, true)?.[0]).toMatchObject({ ratio_num: 2, ratio_den: 1 })
    const two = { ...IN_TAB, entries: [IN_TAB.entries?.[0], IN_TAB.entries?.[0]] } as CaExpected
    expect(entriesFor('bonus', two, true)).toBeNull()
    expect(entriesFor('bonus', { ...IN_TAB, entries: [] }, false)).toBeNull()
    expect(typedRatio('1', '5')).toEqual({ ratio_num: 1, ratio_den: 5 })
    expect(typedRatio('0', '5')).toBeNull()
    expect(typedRatio('1.5', '2')).toBeNull()
  })
})

describe('to confirm', () => {
  it('applies the reading shown to the Upstox account in one tap, and the other way round when chosen', async () => {
    serve()
    show()
    const card = within(await screen.findByRole('region', { name: 'To confirm' }))
    expect(card.getByText('Alpha Industries')).toBeTruthy()
    expect(card.getByText('Bonus')).toBeTruthy()
    expect(card.getAllByText(UPSTOX.words)).toHaveLength(2)
    await userEvent.click(card.getAllByRole('button', { name: 'The other way round' })[0])
    expect(card.getByText(UPSTOX.other?.words ?? '')).toBeTruthy()
    await userEvent.click(card.getByRole('button', { name: 'Yes, apply this bonus' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('ca_decide')[0]).toMatchObject({
      id: 21,
      scope: 'upstox',
      state: 'applied',
      ratio_num: 2,
      ratio_den: 1,
    })
    for (const button of card.getAllByRole('button')) expect(button.className).toContain('min-h-12')
  })

  it('for a tab shows the ready entry first, writes it only on the second tap, then confirms', async () => {
    serve()
    show()
    const card = within(await screen.findByRole('region', { name: 'To confirm' }))
    await userEvent.click(card.getByRole('button', { name: 'See the entry to add to Long term' }))
    expect(await card.findByText('Adds 50 shares at no cost.')).toBeTruthy()
    expect(calls('activity_create')).toHaveLength(1)
    expect(calls('activity_create')[0]).toMatchObject({
      id: 4,
      version: 11,
      entry: IN_TAB.entries?.[0],
      dry_run: true,
    })
    expect(calls('ca_decide')).toHaveLength(0)
    await userEvent.click(card.getByRole('button', { name: 'Add to Long term and confirm' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('activity_create')[1]).toMatchObject({
      id: 4,
      version: 11,
      entry: IN_TAB.entries?.[0],
    })
    expect(calls('activity_create')[1].dry_run).toBeUndefined()
    expect(calls('ca_decide')[0]).toMatchObject({
      scope: 'tab:4',
      state: 'applied',
      ratio_num: 1,
      ratio_den: 2,
    })
  })

  it('leaves the lots as they are when asked, and shows the service’s sentence on a refusal', async () => {
    serve({
      ca_decide: new PortfolioError({
        code: 'refused',
        message: 'This bonus has not happened yet. Confirm it on or after 10 Sep 2026.',
        entryId: null,
      }),
    })
    show()
    const card = within(await screen.findByRole('region', { name: 'To confirm' }))
    await userEvent.click(card.getAllByRole('button', { name: 'Leave my lots as they are' })[0])
    expect((await card.findByRole('alert')).textContent).toMatch(/has not happened yet/)
    expect(calls('ca_decide')[0]).toMatchObject({ state: 'dismissed', scope: 'upstox' })
  })

  it('asks for the ratio when Upstox gave none, and applies nothing until it is two whole numbers', async () => {
    const bare = {
      ...UPSTOX,
      ratio_num: null,
      ratio_den: null,
      other: null,
      words: 'Upstox gave no ratio for this one. Enter it from the company’s notice.',
    }
    serve({ ca_list: { events: [], to_confirm: [{ ...BONUS, expected: [bare] }], reads: [] } })
    show()
    const apply = await screen.findByRole('button', { name: 'Yes, apply this bonus' })
    expect((apply as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByLabelText('New shares'), '1')
    await userEvent.type(screen.getByLabelText('For every held'), '5')
    await userEvent.click(apply)
    await waitFor(() => expect(calls('ca_decide')[0]).toMatchObject({ ratio_num: 1, ratio_den: 5 }))
    expect(screen.queryByRole('button', { name: 'The other way round' })).toBeNull()
  })
})

describe('the calendar', () => {
  it('lists what is coming and the last 90 days by month, each with kind, fact, holding and check', async () => {
    serve({ ca_list: { events: [], to_confirm: [], reads: [] } })
    show()
    const coming = within(await screen.findByRole('region', { name: 'Coming up' }))
    expect(coming.getByText('December 2026')).toBeTruthy()
    expect(coming.getByText('3 Dec 2026 · ₹5.50 a share')).toBeTruthy()
    expect(coming.getByText('You hold 300: about ₹1,650.00')).toBeTruthy()
    expect(coming.getByText('Checked 12 Nov')).toBeTruthy()
    expect(coming.getByText('INE000B01018')).toBeTruthy() // no name: the ISIN
    expect(coming.getByText('Not checked yet')).toBeTruthy()
    const past = within(screen.getByRole('region', { name: 'The last 90 days' }))
    expect(past.getByText('Bonus')).toBeTruthy()
    expect(past.getByText('You held 300')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'To confirm' })).toBeNull()
    expect(screen.queryByRole('note')).toBeNull()
    expect(document.body.textContent).not.toMatch(/Real-time|Live/)
  })

  it('says so when Upstox lists no future events, and when nothing is listed at all', async () => {
    serve({
      ca_list: { events: [], to_confirm: [], reads: [] },
      calendar: { ...CALENDAR, listed_future: false, calendar: [] },
    })
    show()
    expect((await screen.findByRole('note')).textContent).toBe(NO_FUTURE)
    expect(screen.getByText(/No dividend, bonus, split or rights issue is listed/)).toBeTruthy()
  })

  it('says its own sentence while the switch is off', async () => {
    serve({ calendar: new PortfolioError({ code: 'not_ready', message: 'x', entryId: null }) })
    show()
    expect(await screen.findByText(CALENDAR_OFF)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })
})
