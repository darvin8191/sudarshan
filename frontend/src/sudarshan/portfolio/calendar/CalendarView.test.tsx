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
import type {
  CaConfirmed,
  CaDecided,
  CaEvent,
  CaExpected,
  CaList,
  CalendarReport,
  CalendarRow,
  LotCheck,
  LotCheckRow,
  TabInfo,
} from '../types'
import { CALENDAR_OFF, CalendarView, leftOutWords, NO_FUTURE } from './CalendarView'
import { entriesFor, typedRatio, wayOf } from './confirm'
import { factText, holdText } from './format'
import { fixLabel } from './LotCheckCard'

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
  decided: null,
  ...more,
})
const CALENDAR: CalendarReport = {
  from: 20260816,
  to: 20270513,
  listed_future: true,
  read_status: { state: 'ok', night: 20261113, words: null },
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

const NO_CHECK: LotCheck = {
  state: 'ok',
  account_id: 1,
  day: 20261113,
  words: null,
  rows: [],
  unrecorded_count: 0,
}
const LIST: CaList = {
  events: [],
  to_confirm: [],
  confirmed: [],
  left_out_count: 0,
  reads: [],
  read_status: { state: 'ok', night: 20261113, words: null },
  lot_check: NO_CHECK,
}

function serve(over: Record<string, unknown> = {}) {
  const answers: Record<string, unknown> = {
    calendar: CALENDAR,
    ca_list: { ...LIST, to_confirm: [BONUS] },
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
    serve({ ca_list: LIST })
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
      ca_list: LIST,
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

// v0.14.0 review H2, H3, M1, M3: what the list says before it was read, after a failed night, of
// what it leaves out, and when the lots and the holdings differ.
describe('before the first read, and after a failed night', () => {
  const EMPTY = { ...CALENDAR, listed_future: false, calendar: [] }

  it('says when the first read is: no banner that Upstox lists nothing, no empty-list sentence', async () => {
    const words =
      "SUDARSHAN reads Upstox's corporate actions for the first time tonight after 23:45. Nothing is listed until then."
    serve({
      calendar: { ...EMPTY, read_status: { state: 'never', night: null, words } },
      ca_list: { ...LIST, read_status: { state: 'never', night: null, words } },
    })
    show()
    expect(await screen.findByText(words)).toBeInTheDocument()
    expect(screen.queryByText(NO_FUTURE)).toBeNull()
    expect(screen.queryByText(/No dividend, bonus, split or rights issue is listed/)).toBeNull()
  })

  it('says the list could not be read last night, in the service’s words, never "has not listed"', async () => {
    const words =
      "Upstox's list could not be read last night. Upstox did not allow SUDARSHAN to read its list of events with the read-only token. SUDARSHAN tries again tonight after 23:45."
    serve({ calendar: { ...EMPTY, read_status: { state: 'failed', night: 20261113, words } } })
    show()
    expect(await screen.findByText(words)).toBeInTheDocument()
    expect(screen.queryByText(NO_FUTURE)).toBeNull()
    expect(screen.queryByText(/is listed for what you hold/)).toBeNull()
  })

  it('keeps the banner and the empty sentence for a list that was read and is empty', async () => {
    serve({ calendar: EMPTY, ca_list: LIST })
    show()
    expect(await screen.findByText(NO_FUTURE)).toBeInTheDocument()
    expect(
      screen.getByText(/No dividend, bonus, split or rights issue is listed/)
    ).toBeInTheDocument()
  })
})

describe('what is left out, and the check', () => {
  it('counts the bonuses and splits it cannot ask about yet, and says why', async () => {
    expect(leftOutWords(1)).toBe(
      '1 earlier bonus or split is not asked about yet: SUDARSHAN cannot tell what you held on its date until Upstox’s share trades are read in full.'
    )
    serve({ ca_list: { ...LIST, left_out_count: 3 } })
    show()
    expect(
      await screen.findByText(
        '3 earlier bonuses or splits are not asked about yet: SUDARSHAN cannot tell what you held on their dates until Upstox’s share trades are read in full.'
      )
    ).toBeInTheDocument()
  })

  it('says where a quantity comes from when no holdings record reaches the date', async () => {
    serve({
      ca_list: {
        ...LIST,
        to_confirm: [{ ...BONUS, expected: [{ ...UPSTOX, held_from: 'trades' }] }],
      },
    })
    show()
    expect(
      await screen.findByText(
        'Quantity from Upstox’s trade history: no holdings record reaches this date.'
      )
    ).toBeInTheDocument()
  })

  const TWICE: LotCheckRow = {
    isin: 'INE000A01010',
    name: 'Alpha Industries',
    held_qty_u: 500_000_000,
    lots_qty_u: 2_500_000_000,
    ex_day: 20260928,
    words:
      'Alpha Industries: after the split of 28 Sep 2026 the lots say 2,500 and Upstox holds 500. Without it the lots would say 500: it may have been counted twice.',
    fix: { event_id: 41, version: 1, state: 'dismissed', ratio_num: null, ratio_den: null },
    event_id: null,
  }
  const differ = (rows: LotCheckRow[]) => ({
    ca_list: {
      ...LIST,
      lot_check: { ...NO_CHECK, day: 20261021, words: 'Lots and holdings differ.', rows },
    },
  })

  it('shows a Check card naming the share and both figures when lots and holdings differ', async () => {
    serve(differ([TWICE]))
    show()
    const card = await screen.findByRole('region', { name: 'Check' })
    expect(within(card).getByText(TWICE.words)).toBeInTheDocument()
    expect(
      within(card).getByText(/differ for 1 share, compared at the close of 21 Oct 2026/)
    ).toBeInTheDocument()
  })

  it('offers the one change the service proved, sends it from the decision’s version, then reads again', async () => {
    const user = userEvent.setup()
    serve(differ([TWICE]))
    show()
    const card = await screen.findByRole('region', { name: 'Check' })
    const before = calls('ca_list').length
    await user.click(within(card).getByRole('button', { name: 'Take this one out of my lots' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('ca_decide')[0]).toEqual({
      id: 41,
      scope: 'upstox',
      state: 'dismissed',
      ratio_num: 1,
      ratio_den: 1,
      version: 1,
    })
    await waitFor(() => expect(calls('ca_list').length).toBeGreaterThan(before))
  })

  it('turns a wrong-way split round, and offers nothing where no change makes the two agree', async () => {
    const user = userEvent.setup()
    const wrong: LotCheckRow = {
      ...TWICE,
      isin: 'INE000B01011',
      fix: { event_id: 42, version: 3, state: 'applied', ratio_num: 5, ratio_den: 1 },
    }
    const none: LotCheckRow = { ...TWICE, isin: 'INE000C01012', name: 'Gamma Ltd', fix: null }
    expect(fixLabel(wrong)).toBe('Apply it the other way round')
    expect(fixLabel(none)).toBeNull()
    serve(differ([wrong, none]))
    show()
    const card = await screen.findByRole('region', { name: 'Check' })
    expect(within(card).getAllByRole('button')).toHaveLength(1)
    await user.click(within(card).getByRole('button', { name: 'Apply it the other way round' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('ca_decide')[0]).toMatchObject({
      id: 42,
      state: 'applied',
      ratio_num: 5,
      ratio_den: 1,
      version: 3,
    })
  })

  it('says the service’s sentence when the change is refused, and reads nothing again', async () => {
    const user = userEvent.setup()
    serve({
      ...differ([TWICE]),
      ca_decide: new PortfolioError({
        code: 'version_conflict',
        message: 'This was changed elsewhere. Reload the page.',
        entryId: null,
      }),
    })
    show()
    const card = await screen.findByRole('region', { name: 'Check' })
    await user.click(within(card).getByRole('button', { name: 'Take this one out of my lots' }))
    expect((await within(card).findByRole('alert')).textContent).toBe(
      'This was changed elsewhere. Reload the page.'
    )
  })

  it('shows no Check card while the lots and the holdings agree', async () => {
    serve({ ca_list: LIST })
    show()
    await screen.findByText('Coming up')
    expect(screen.queryByRole('region', { name: 'Check' })).toBeNull()
  })
})

// v0.14.0 verification M3, N1: every bonus and split confirmed for the Upstox account is listed
// under Confirmed, whatever its day, and changed there from its version with To confirm's own
// choices; the one that stands is not offered again, and a Check row that asks for a look
// again opens the same choices.
describe('what was confirmed', () => {
  const SPLIT_WORDS = 'Split 1:5: you held 100, they become 500. Is that right?'
  const decided = (more: Partial<CaDecided> = {}): CaDecided => ({
    event_id: 7,
    version: 3,
    state: 'applied',
    ratio_num: 1,
    ratio_den: 5,
    words: 'You applied this split as every 5 made 1: 100 became 20.',
    expected: {
      scope: 'upstox',
      held_qty_u: 100_000_000,
      ratio_num: 5,
      ratio_den: 1,
      words: SPLIT_WORDS,
      after_qty_u: 500_000_000,
      held_from: 'trades',
      other: {
        ratio_num: 1,
        ratio_den: 5,
        words: 'Split 1:5: you held 100, they become 20. Is that right?',
        after_qty_u: 20_000_000,
      },
    },
    ...more,
  })
  /** A split of 1 Jun 2026: older than the 90 days the calendar shows. */
  const kept = (more: Partial<CaDecided> = {}, gone: number | null = null): CaConfirmed => ({
    event_id: 7,
    isin: 'INE000D01013',
    name: 'Delta Motors',
    kind: 'split',
    ex_day: 20260601,
    ratio_num: 1,
    ratio_den: 5,
    gone_at: gone,
    decided: decided(more),
  })
  const listing = (rows: CaConfirmed[], more: Partial<CaList> = {}) => ({
    ca_list: { ...LIST, confirmed: rows, ...more },
  })

  it('lists one older than the calendar’s 90 days and sends the other reading from its version', async () => {
    const user = userEvent.setup()
    serve(listing([kept()]))
    show()
    const list = within(await screen.findByRole('region', { name: 'Confirmed' }))
    expect(list.getByText('Delta Motors')).toBeInTheDocument()
    expect(list.getByText('Ex-date 1 Jun 2026 · Ratio 1:5, as Upstox lists it')).toBeInTheDocument()
    expect(list.getByText(decided().words)).toBeInTheDocument()
    await user.click(list.getByRole('button', { name: 'Change what I confirmed' }))
    expect(list.getByText(SPLIT_WORDS)).toBeInTheDocument()
    expect(
      list.getByText('Quantity from Upstox’s trade history: no holdings record reaches this date.')
    ).toBeInTheDocument()
    await user.click(list.getByRole('button', { name: 'Yes, apply this split' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('ca_decide')[0]).toEqual({
      id: 7,
      scope: 'upstox',
      state: 'applied',
      ratio_num: 5,
      ratio_den: 1,
      version: 3,
    })
    for (const button of list.getAllByRole('button')) expect(button.className).toContain('min-h-12')
  })

  it('does not offer the reading applied now, or leaving the lots as they are when that stands', async () => {
    const user = userEvent.setup()
    serve(listing([kept()]))
    show()
    const list = within(await screen.findByRole('region', { name: 'Confirmed' }))
    await user.click(list.getByRole('button', { name: 'Change what I confirmed' }))
    await user.click(list.getByRole('button', { name: 'The other way round' }))
    expect(list.getByText('This is the reading applied now.')).toBeInTheDocument()
    expect(list.queryByRole('button', { name: 'Yes, apply this split' })).toBeNull()
    await user.click(list.getByRole('button', { name: 'Leave my lots as they are' }))
    await waitFor(() => expect(calls('ca_decide')[0]).toMatchObject({ state: 'dismissed' }))
    expect(calls('ca_decide')[0].version).toBe(3)
  })

  it('lists one Upstox no longer lists, and offers putting back one left out of the lots', async () => {
    const user = userEvent.setup()
    const left = kept(
      {
        state: 'dismissed',
        ratio_num: null,
        ratio_den: null,
        words: 'You left your lots as they are for this split.',
      },
      Date.UTC(2026, 9, 20)
    )
    serve(listing([left]))
    show()
    const list = within(await screen.findByRole('region', { name: 'Confirmed' }))
    expect(
      list.getByText('Upstox no longer lists it; what you confirmed still counts.')
    ).toBeInTheDocument()
    await user.click(list.getByRole('button', { name: 'Change what I confirmed' }))
    expect(list.getByRole('button', { name: 'Yes, apply this split' })).toBeInTheDocument()
    expect(list.queryByRole('button', { name: 'Leave my lots as they are' })).toBeNull()
    await user.click(list.getByRole('button', { name: 'Keep it as it is' }))
    expect(list.queryByRole('button', { name: 'Yes, apply this split' })).toBeNull()
    expect(calls('ca_decide')).toHaveLength(0)
  })

  it('leads a Check row that asks for a look again to the same choices', async () => {
    const user = userEvent.setup()
    const neither: LotCheckRow = {
      isin: 'INE000D01013',
      name: 'Delta Motors',
      held_qty_u: 750_000_000,
      lots_qty_u: 20_000_000,
      ex_day: 20260601,
      words:
        'Delta Motors: after the split of 1 Jun 2026 the lots say 20 and Upstox holds 750. Neither reading of it makes the two agree: a purchase or a sale may be missing from the records. You can see the reading you confirmed, and change it, under Confirmed in the Calendar.',
      fix: null,
      event_id: 7,
    }
    serve(
      listing([kept()], {
        lot_check: {
          ...NO_CHECK,
          day: 20261021,
          words: 'Lots and holdings differ.',
          rows: [neither],
        },
      })
    )
    show()
    const card = within(await screen.findByRole('region', { name: 'Check' }))
    await user.click(card.getByRole('button', { name: 'Change what I confirmed' }))
    await user.click(card.getByRole('button', { name: 'Yes, apply this split' }))
    await waitFor(() => expect(calls('ca_decide')).toHaveLength(1))
    expect(calls('ca_decide')[0]).toMatchObject({ id: 7, ratio_num: 5, ratio_den: 1, version: 3 })
  })

  it('says on a calendar row what stands, and keeps the change under Confirmed', async () => {
    const row8 = row({
      event_id: 8,
      ex_day: 20261009,
      kind: 'split',
      amount_u: null,
      ratio_num: 1,
      ratio_den: 5,
      expected_paise: null,
      decided: decided({ event_id: 8 }),
    })
    serve({ ca_list: LIST, calendar: { ...CALENDAR, calendar: [row8] } })
    show()
    const past = within(await screen.findByRole('region', { name: 'The last 90 days' }))
    expect(past.getByText(decided().words)).toBeInTheDocument()
    expect(past.queryByRole('button', { name: 'Change what I confirmed' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Confirmed' })).toBeNull() // nothing listed
  })
})
