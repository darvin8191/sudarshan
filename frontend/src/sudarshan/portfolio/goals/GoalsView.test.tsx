/**
 * The Goals view (P7 F8), against lane R's answer shape: the card's words, the bar and the amount
 * moving with the linked tabs' ticks (and "Real-time" only while they arrive), a typed amount that
 * says it does not move, the form in the service's units, archived goals folded, the switch-off
 * sentence.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  report: null as null | ((tabId: number, now: object) => void),
  mounted: [] as number[],
}))
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listGoals: api.list,
  createGoal: api.create,
  updateGoal: api.update,
}))
vi.mock('./live', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./live')>()),
  TabValue: ({ tabId, onValue }: { tabId: number; onValue: (id: number, now: object) => void }) => {
    api.report = onValue
    if (!api.mounted.includes(tabId)) api.mounted.push(tabId)
    return null
  },
}))
vi.mock('@/sudarshan/dashboard/MarketCalendar', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/sudarshan/dashboard/MarketCalendar')>()),
  MarketCalendarSource: () => null,
}))
vi.mock('@/sudarshan/watchlist/openGuard', () => ({
  useOpenGuard: () => ({ settling: false, handlers: {} }),
}))

import { createQueryClient } from '@/app/providers'
import { PortfolioError } from '../api'
import type { Goal, GoalsList, TabInfo } from '../types'
import { fieldsOf, formOf, GONE_TAB, TARGET_BIG, targetTooBig } from './GoalSheet'
import { GOALS_OFF, GoalsView } from './GoalsView'
import { goalNow, lineKey, sameTabNow, type TabNow } from './live'
import { addWords, byWords, figuresNow, shownProgress } from './sums'

const TABS = [
  { id: 4, name: 'Long term', version: 1 },
  { id: 5, name: 'Gold', version: 1 },
] as TabInfo[]
const goal = (more: Partial<Goal> = {}): Goal => ({
  id: 1,
  member_id: 1,
  name: 'House',
  name_alt: 'ઘર',
  target_paise: 10_00_000_00,
  target_day: 20310331,
  rate_bp: 1200,
  tab_ids: [4, 5],
  fixed_paise: null,
  archived: false,
  version: 2,
  value_now_paise: 2_00_000_00,
  progress_pct: '20.0000',
  required_monthly_paise: 8_060_00,
  months_left: 52,
  on_track: false,
  gone_tab_ids: [],
  problem: null,
  ...more,
})
const list = (...goals: Goal[]): GoalsList => ({ as_of: Date.UTC(2026, 10, 14, 10), goals })
const LIVE = { kind: 'live' } as TabNow['line']
const CLOSED = { kind: 'closed' } as TabNow['line']

const IDLE = { kind: 'idle' } as TabNow['line']
const OLD = { id: 6, name: 'Old savings', version: 3, archived: true } as TabInfo
const TODAY = 20261114

function show(archivedTabs: TabInfo[] = []) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <GoalsView tabs={TABS} archivedTabs={archivedTabs} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  api.list.mockReset().mockResolvedValue({ enabled: true, data: list(goal()) })
  api.create.mockReset()
  api.update.mockReset()
  api.report = null
  api.mounted = []
})

describe('the words and the sums', () => {
  it('the date with the months left, and what to add or that it is on track', () => {
    expect(byWords(goal(), TODAY)).toBe('By March 2031 · 52 months left')
    expect(byWords(goal({ months_left: 1 }), TODAY)).toBe('By March 2031 · 1 month left')
    // Whole rupees: the amount is rounded up to the rupee, so it prints no paise (review L2).
    expect(addWords(goal(), 2_00_000_00, TODAY)).toBe(
      'Add about ₹8,060 a month at 12% a year (your assumption)'
    )
    expect(addWords(goal({ on_track: true, required_monthly_paise: 0 }), 2_00_000_00, TODAY)).toBe(
      'On track without adding more'
    )
  })

  it('a goal past its date says so and what is left, never "0 months left" or an amount "a month"', () => {
    // Review M3: "By September 2026 · 0 months left. Add about ₹49,602.00 a month".
    const late = goal({
      target_day: 20260930,
      months_left: 0,
      target_paise: 1_00_000_00,
      value_now_paise: 50_000_00,
      required_monthly_paise: 49_602_00,
    })
    expect(byWords(late, TODAY)).toBe('Was due by September 2026 · its date has passed')
    expect(addWords(late, 50_000_00, TODAY)).toBe(
      '₹50,000.00 is still to be added. Open the goal to choose a new date.'
    )
    expect(addWords(late, 1_00_000_00, TODAY)).toBe('Target reached')
    // Due within a month: not overdue, and still no "a month".
    const soon = { ...late, target_day: 20261130 }
    expect(byWords(soon, TODAY)).toBe('By November 2026 · less than a month left')
    expect(addWords(soon, 50_000_00, TODAY)).toBe(
      'Add about ₹49,602 by the date at 12% a year (your assumption)'
    )
    for (const each of [late, soon]) {
      const said = `${byWords(each, TODAY)} ${addWords(each, 50_000_00, TODAY)}`
      expect(said).not.toMatch(/0 months|₹[\d,.]+ a month/)
    }
  })

  it('the percentage is cut, never rounded up, below 100', () => {
    // Review L2: 99.96% printed "100.0%" above "Add about ₹1.00 a month".
    expect(shownProgress('99.9600')).toBe(99.9)
    expect(shownProgress('99.9999')).toBe(99.9)
    expect(shownProgress('9.7000')).toBe(9.7) // by its digits: 9.7 x 10 is 96.99... as a float
    expect(shownProgress('20.0000')).toBe(20)
    expect(shownProgress('100.0000')).toBe(100)
    expect(shownProgress('125.0400')).toBe(125.04)
    expect(shownProgress(null)).toBe(0)
  })

  it('the amount to add follows the value shown; with the value as read it is the service’s own', () => {
    expect(figuresNow(goal(), 2_00_000_00)).toEqual({
      required_monthly_paise: 8_060_00,
      on_track: false,
    })
    // 6,00,000 at 12% for 52 months grows past 10,00,000: nothing to add.
    expect(figuresNow(goal(), 6_50_000_00)).toEqual({ required_monthly_paise: 0, on_track: true })
    const less = figuresNow(goal(), 2_10_000_00)
    const more = figuresNow(goal(), 2_50_000_00)
    expect(more.required_monthly_paise).toBeLessThan(less.required_monthly_paise)
    expect(more.required_monthly_paise % 100).toBe(0)
    expect(more.on_track).toBe(false)
  })

  it('a goal is worth its tabs now once all have reported; before that, and for a typed amount, as read', () => {
    const one = new Map<number, TabNow>([[4, { value_paise: 1_50_000_00, line: LIVE }]])
    expect(goalNow(goal(), one)).toMatchObject({ value_paise: 2_00_000_00, line: null })
    const both = new Map(one).set(5, { value_paise: 1_00_000_00, line: CLOSED })
    expect(goalNow(goal(), both)).toEqual({
      value_paise: 2_50_000_00,
      progress_pct: '25.0000',
      line: CLOSED, // not Real-time unless every linked tab that can tick is ticking
      basis: 'ticks',
    })
    expect(goalNow(goal({ fixed_paise: 5_00_000_00, tab_ids: [] }), both)).toMatchObject({
      line: null,
      basis: 'typed',
    })
  })

  it('a tab with nothing to tick does not silence the others; with every tab so, the card says so', () => {
    // Review M1: shares plus a tab of gold only moved with ticks and said nothing at all.
    const mixed = new Map<number, TabNow>([
      [4, { value_paise: 1_50_000_00, line: LIVE }],
      [5, { value_paise: 1_00_000_00, line: IDLE }],
    ])
    expect(goalNow(goal(), mixed)).toMatchObject({ line: LIVE, basis: 'ticks' })
    const stale = { kind: 'delayed', at: 5, stale: true } as TabNow['line']
    const late = new Map(mixed).set(4, { value_paise: 1_50_000_00, line: stale })
    expect(goalNow(goal(), late)).toMatchObject({ line: stale, basis: 'ticks' })
    const closed = new Map(mixed).set(4, { value_paise: 1_50_000_00, line: CLOSED })
    expect(goalNow(goal(), closed).line).toBe(CLOSED)
    const still = new Map(mixed).set(4, { value_paise: 1_50_000_00, line: IDLE })
    expect(goalNow(goal(), still)).toMatchObject({
      value_paise: 2_50_000_00,
      line: null,
      basis: 'still',
    })
  })

  it('a deleted tab counts as nothing and is not waited for', () => {
    const one = new Map<number, TabNow>([[4, { value_paise: 1_50_000_00, line: LIVE }]])
    expect(goalNow(goal({ gone_tab_ids: [5] }), one)).toMatchObject({
      value_paise: 1_50_000_00,
      line: LIVE,
    })
    expect(goalNow(goal({ gone_tab_ids: [4, 5], value_now_paise: 0 }), one)).toMatchObject({
      value_paise: 0,
      basis: 'read',
    })
  })

  it('two reports that say the same are the same, whichever objects say it', () => {
    const a = { kind: 'delayed', at: 5, stale: false } as TabNow['line']
    expect(sameTabNow({ value_paise: 1, line: a }, { value_paise: 1, line: { ...a } })).toBe(true)
    expect(sameTabNow({ value_paise: 1, line: a }, { value_paise: 2, line: a })).toBe(false)
    expect(lineKey(a)).not.toBe(lineKey({ ...a, stale: true } as TabNow['line']))
    expect(lineKey(a)).not.toBe(lineKey({ ...a, at: 6 } as TabNow['line']))
    expect(lineKey(CLOSED)).not.toBe(lineKey({ kind: 'closed', stoppedAt: 9 }))
    expect(lineKey({ kind: 'connecting', at: null, stale: false, again: false })).not.toBe(
      lineKey({ kind: 'connecting', at: null, stale: false, again: true })
    )
  })

  it('a target with too many zeros is told before it is sent', () => {
    expect(targetTooBig('5,00,000')).toBe(false)
    expect(targetTooBig('10000000000')).toBe(false) // ₹1,000 crore itself
    expect(targetTooBig('10000000001')).toBe(true)
    expect(targetTooBig('1'.padEnd(40, '0'))).toBe(true) // too big to hold as a number at all
    expect(targetTooBig('abc')).toBe(false) // not a number: the service's own sentence
  })

  it('the form goes in the service’s units: tabs, or the typed amount, never both', () => {
    expect(formOf(null).rate).toBe('10')
    expect(formOf(goal())).toMatchObject({
      target: '1000000',
      day: '2031-03-31',
      rate: '12',
      tabs: [4, 5],
      fixed: '',
    })
    const form = {
      ...formOf(null),
      name: ' Car ',
      target: '5,00,000',
      day: '2028-06-30',
      fixed: '1,20,000',
    }
    expect(fieldsOf(form)).toEqual({
      name: 'Car',
      name_alt: null,
      target_paise: 5_00_000_00,
      target_day: 20280630,
      rate_bp: 1000,
      tab_ids: [],
      fixed_paise: 1_20_000_00,
    })
    expect(fieldsOf({ ...form, tabs: [4] })).toMatchObject({ tab_ids: [4], fixed_paise: null })
  })
})

describe('the view', () => {
  it('shows a goal as read, then moves its bar and amount with its tabs and says Real-time only then', async () => {
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(card.getByText('ઘર')).toBeTruthy()
    expect(card.getByText('20.0%')).toBeTruthy()
    expect(card.getByText('₹2,00,000.00')).toBeTruthy()
    expect(card.getByText('₹10,00,000.00').className).toContain('whitespace-nowrap')
    expect(card.getByText('By March 2031 · 52 months left')).toBeTruthy()
    expect(card.getByText('As the service last read it.')).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull()
    expect(api.mounted).toEqual([4, 5]) // each linked tab priced once, however many goals count it
    act(() => {
      api.report?.(4, { value_paise: 1_50_000_00, line: LIVE })
      api.report?.(5, { value_paise: 1_00_000_00, line: LIVE })
    })
    expect(await card.findByText('25.0%')).toBeTruthy()
    expect(card.getByText('₹2,50,000.00')).toBeTruthy()
    expect(card.getByText('Real-time')).toBeTruthy()
    expect(card.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25')
    act(() => api.report?.(5, { value_paise: 1_00_000_00, line: CLOSED }))
    expect(await card.findByText('Market closed')).toBeTruthy()
    expect(card.queryByText('Real-time')).toBeNull()
  })

  it('a typed amount says it does not move, and a goal past its target keeps its figure with a full bar', async () => {
    api.list.mockResolvedValue({
      enabled: true,
      data: list(
        goal({
          fixed_paise: 12_00_000_00,
          tab_ids: [],
          value_now_paise: 12_00_000_00,
          progress_pct: '120.0000',
          on_track: true,
        })
      ),
    })
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(
      card.getByText('Counts the amount you typed; it does not move with prices.')
    ).toBeTruthy()
    expect(card.getByText('120.0%')).toBeTruthy()
    expect(card.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100')
    expect(card.getByText('On track without adding more')).toBeTruthy()
    expect(api.mounted).toEqual([])
  })

  it('adds a goal from the form and shows the list the service answers', async () => {
    api.list.mockResolvedValue({ enabled: true, data: list() })
    api.create.mockResolvedValue({ enabled: true, data: list(goal({ name: 'Car' })) })
    show()
    expect(await screen.findByText(/No goal yet/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Goal' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.type(sheet.getByLabelText('Name'), 'Car')
    await userEvent.type(sheet.getByLabelText('Target, in rupees'), '500000')
    await userEvent.click(sheet.getByLabelText('Long term'))
    expect(sheet.queryByLabelText(/Or the amount saved so far/)).toBeNull()
    await userEvent.click(sheet.getByRole('button', { name: 'Add goal' }))
    await waitFor(() => expect(api.create).toHaveBeenCalled())
    expect(api.create.mock.lastCall?.[0]).toMatchObject({
      name: 'Car',
      target_paise: 5_00_000_00,
      tab_ids: [4],
      fixed_paise: null,
      rate_bp: 1000,
    })
    expect(api.create.mock.lastCall?.[0].request_id).toMatch(/[0-9a-f-]{36}/)
    expect(await screen.findByRole('article', { name: 'Car' })).toBeTruthy()
  })

  it('changes and archives a goal from the version read, and folds archived goals away', async () => {
    api.list.mockResolvedValue({
      enabled: true,
      data: list(goal(), goal({ id: 2, name: 'Old plan', archived: true })),
    })
    api.update.mockResolvedValue({ enabled: true, data: list(goal({ archived: true })) })
    show()
    await screen.findByRole('article', { name: 'House' })
    expect(screen.queryByRole('article', { name: 'Old plan' })).toBeNull()
    expect(screen.getByText('Archived goals (1)')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Change House' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(sheet.getByRole('button', { name: 'Archive this goal' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith({ id: 1, version: 2, archived: true })
    )
    await waitFor(() => expect(screen.queryByRole('article', { name: 'House' })).toBeNull())
  })

  it('shows the service’s sentence when a value is refused, and its own while the switch is off', async () => {
    api.create.mockRejectedValue(
      new PortfolioError({
        code: 'bad_request',
        message: 'Choose a target date after today.',
        entryId: null,
      })
    )
    api.list.mockResolvedValue({ enabled: true, data: list() })
    const { unmount } = show()
    await userEvent.click(await screen.findByRole('button', { name: 'Goal' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(sheet.getByRole('button', { name: 'Add goal' }))
    expect((await sheet.findByRole('alert')).textContent).toBe('Choose a target date after today.')
    unmount()
    api.list.mockRejectedValue(
      new PortfolioError({ code: 'not_ready', message: 'x', entryId: null })
    )
    show()
    expect(await screen.findByText(GOALS_OFF)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Goal' })).toBeNull()
  })
})

describe('what a card and the form say when something is not as it was', () => {
  it('a deleted tab is said on the card, and the form names it and lets it go', async () => {
    // Review M2: "0.0%" with no word, every tab unticked and no amount box.
    api.list.mockResolvedValue({
      enabled: true,
      data: list(
        goal({ tab_ids: [9], gone_tab_ids: [9], value_now_paise: 0, progress_pct: '0.0000' })
      ),
    })
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(
      card.getByText(
        'A tab this goal counted was deleted and counts as nothing now. Open the goal to unlink it.'
      )
    ).toBeTruthy()
    expect(api.mounted).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Change House' }))
    const sheet = within(await screen.findByRole('dialog'))
    const lost = sheet.getByLabelText(GONE_TAB) as HTMLInputElement
    expect(lost.checked).toBe(true)
    expect(sheet.queryByLabelText(/Or the amount saved so far/)).toBeNull()
    await userEvent.click(lost)
    await userEvent.type(sheet.getByLabelText(/Or the amount saved so far/), '50000')
    api.update.mockResolvedValue({ enabled: true, data: list(goal()) })
    await userEvent.click(sheet.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.update).toHaveBeenCalled())
    expect(api.update.mock.lastCall?.[0]).toMatchObject({ tab_ids: [], fixed_paise: 50_000_00 })
  })

  it('an archived tab is named on the card and in the form, still ticked, and is still priced', async () => {
    api.list.mockResolvedValue({ enabled: true, data: list(goal({ tab_ids: [4, 6] })) })
    show([OLD])
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(
      card.getByText('Counts Old savings, a tab that is archived. It still counts what it holds.')
    ).toBeTruthy()
    expect(api.mounted).toEqual([4, 6])
    await userEvent.click(screen.getByRole('button', { name: 'Change House' }))
    const sheet = within(await screen.findByRole('dialog'))
    const old = sheet.getByLabelText('Old savings (archived)') as HTMLInputElement
    expect(old.checked).toBe(true)
    await userEvent.click(old)
    api.update.mockResolvedValue({ enabled: true, data: list(goal()) })
    await userEvent.click(sheet.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.update).toHaveBeenCalled())
    expect(api.update.mock.lastCall?.[0]).toMatchObject({ tab_ids: [4], fixed_paise: null })
  })

  it('an overdue goal says its date has passed and what is left', async () => {
    api.list.mockResolvedValue({
      enabled: true,
      data: list(
        goal({
          fixed_paise: 50_000_00,
          tab_ids: [],
          target_paise: 1_00_000_00,
          target_day: 20200131,
          months_left: 0,
          value_now_paise: 50_000_00,
          progress_pct: '50.0000',
          required_monthly_paise: 49_602_00,
        })
      ),
    })
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(card.getByText('Was due by January 2020 · its date has passed')).toBeTruthy()
    expect(
      card.getByText('₹50,000.00 is still to be added. Open the goal to choose a new date.')
    ).toBeTruthy()
    expect(card.queryByText(/0 months|₹[\d,.]+ a month/)).toBeNull()
  })

  it('99.96% is not printed as 100.0% above an amount still to add', async () => {
    api.list.mockResolvedValue({
      enabled: true,
      data: list(
        goal({
          fixed_paise: 9_99_600_00,
          tab_ids: [],
          value_now_paise: 9_99_600_00,
          progress_pct: '99.9600',
          required_monthly_paise: 1_00,
        })
      ),
    })
    show()
    const card = within(await screen.findByRole('article', { name: 'House' }))
    expect(card.getByText('99.9%')).toBeTruthy()
    expect(card.queryByText('100.0%')).toBeNull()
    expect(card.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('99')
    expect(card.getByText(/Add about ₹1 a month/)).toBeTruthy()
  })

  it('a goal whose figures could not be worked out says so, beside the others, and can be archived', async () => {
    const said = 'This goal’s figures could not be worked out. Check its target, or archive it.'
    api.list.mockResolvedValue({
      enabled: true,
      data: list(goal({ id: 7, name: 'Bad', problem: said }), goal({ id: 8, name: 'Car' })),
    })
    api.update.mockResolvedValue({ enabled: true, data: list(goal({ id: 8, name: 'Car' })) })
    show()
    const bad = within(await screen.findByRole('article', { name: 'Bad' }))
    expect(bad.getByText(said)).toBeTruthy()
    expect(bad.queryByRole('progressbar')).toBeNull()
    expect(bad.queryByText(/NaN|Infinity|∞/)).toBeNull()
    expect(screen.getByRole('article', { name: 'Car' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Change Bad' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(sheet.getByRole('button', { name: 'Archive this goal' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith({ id: 7, version: 2, archived: true })
    )
  })

  it('a target with too many zeros is refused with a sentence and nothing is sent', async () => {
    api.list.mockResolvedValue({ enabled: true, data: list() })
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Goal' }))
    const sheet = within(await screen.findByRole('dialog'))
    await userEvent.type(sheet.getByLabelText('Name'), 'Car')
    await userEvent.type(sheet.getByLabelText('Target, in rupees'), '1'.padEnd(30, '0'))
    await userEvent.click(sheet.getByRole('button', { name: 'Add goal' }))
    expect((await sheet.findByRole('alert')).textContent).toBe(TARGET_BIG)
    expect(api.create).not.toHaveBeenCalled()
  })

  it('after "changed on another screen" the list is read again, so opening the goal again has the new version', async () => {
    // Review L4: the sheet kept the old version until he left the view.
    api.update.mockRejectedValueOnce(
      new PortfolioError({
        code: 'version_conflict',
        message: 'This item was changed on another screen.',
        entryId: null,
      })
    )
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Change House' }))
    let sheet = within(await screen.findByRole('dialog'))
    api.list.mockResolvedValue({ enabled: true, data: list(goal({ version: 3, name: 'House' })) })
    await userEvent.click(sheet.getByRole('button', { name: 'Archive this goal' }))
    expect((await sheet.findByRole('alert')).textContent).toMatch(/changed on another screen/)
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2))
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    api.update.mockResolvedValue({ enabled: true, data: list() })
    await userEvent.click(screen.getByRole('button', { name: 'Change House' }))
    sheet = within(await screen.findByRole('dialog'))
    await userEvent.click(sheet.getByRole('button', { name: 'Archive this goal' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenLastCalledWith({ id: 1, version: 3, archived: true })
    )
  })
})
