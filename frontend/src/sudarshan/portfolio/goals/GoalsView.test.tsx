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
import { fieldsOf, formOf } from './GoalSheet'
import { addWords, byWords, GOALS_OFF, GoalsView } from './GoalsView'
import { goalNow, type TabNow } from './live'

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
  ...more,
})
const list = (...goals: Goal[]): GoalsList => ({ as_of: Date.UTC(2026, 10, 14, 10), goals })
const LIVE = { kind: 'live' } as TabNow['line']
const CLOSED = { kind: 'closed' } as TabNow['line']

function show() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <GoalsView tabs={TABS} />
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
    expect(byWords(goal())).toBe('By March 2031 · 52 months left')
    expect(byWords(goal({ months_left: 1 }))).toBe('By March 2031 · 1 month left')
    expect(addWords(goal())).toBe('Add about ₹8,060.00 a month at 12% a year (your assumption)')
    expect(addWords(goal({ on_track: true, required_monthly_paise: 0 }))).toBe(
      'On track without adding more'
    )
  })

  it('a goal is worth its tabs now once all have reported; before that, and for a typed amount, as read', () => {
    const one = new Map<number, TabNow>([[4, { value_paise: 1_50_000_00, line: LIVE, now: 5 }]])
    expect(goalNow(goal(), one)).toMatchObject({ value_paise: 2_00_000_00, line: null })
    const both = new Map(one).set(5, { value_paise: 1_00_000_00, line: CLOSED, now: 9 })
    expect(goalNow(goal(), both)).toEqual({
      value_paise: 2_50_000_00,
      progress_pct: '25.0000',
      line: CLOSED, // not Real-time unless every linked tab's ticks are arriving
      now: 9,
    })
    expect(goalNow(goal({ fixed_paise: 5_00_000_00, tab_ids: [] }), both).line).toBeNull()
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
      api.report?.(4, { value_paise: 1_50_000_00, line: LIVE, now: 1 })
      api.report?.(5, { value_paise: 1_00_000_00, line: LIVE, now: 2 })
    })
    expect(await card.findByText('25.0%')).toBeTruthy()
    expect(card.getByText('₹2,50,000.00')).toBeTruthy()
    expect(card.getByText('Real-time')).toBeTruthy()
    expect(card.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25')
    act(() => api.report?.(5, { value_paise: 1_00_000_00, line: CLOSED, now: 3 }))
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
