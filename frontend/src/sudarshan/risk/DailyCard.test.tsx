/**
 * The daily loss card (M5.14): today's figure with its sign, an arrow and a word as well as the
 * colour; when it was read and how long ago, never as live (rule 7); outside the session, when
 * it is checked again instead of "Reached"; the limit, or a suggestion when there is none (R4:
 * the owner picks the figure); the day's lock; and settings or a figure that could not be read,
 * never a number or an "Off" passed off as current.
 */

import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DailyStatus, RiskStatus } from '@/api/risk'
import {
  DAILY_SUGGESTION,
  DailyCard,
  DELIVERY_NOT_COUNTED,
  LIMIT_UNREADABLE,
  READ_NOTE,
} from './DailyCard'

const READ_AT = '2026-10-28T05:12:07+00:00' // 10:42:07 in India
const RECEIVED = Date.parse(READ_AT)

function day(over: Partial<DailyStatus> = {}): DailyStatus {
  return {
    figure: -3250.5,
    approximate: false,
    read_at: READ_AT,
    in_session: true,
    opens_at: null,
    reached: false,
    lock: null,
    unchecked: false,
    ...over,
  }
}

function status(limit: number | null, daily: RiskStatus['daily'], read = true): RiskStatus {
  return {
    stage: 'on',
    kill_switch: { known: true, on: false },
    settings: read ? ({ daily_loss_limit: limit } as RiskStatus['settings']) : null,
    gate: { orders: 0, p50_ms: null, p95_ms: null, max_ms: null, last_problem: null },
    upstox_kill_switch: null,
    broker: 'upstox',
    daily,
  }
}

function card(over: RiskStatus) {
  return render(<DailyCard status={over} receivedAt={RECEIVED} />)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(RECEIVED)
})
afterEach(() => vi.useRealTimers())

describe('the daily loss card', () => {
  it("shows today's figure with its sign, an arrow and a word, read when and how long ago", () => {
    card(status(4000, { live: day() }))
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent('−₹3,250.50loss today')
    expect(screen.getByTestId('daily-read-live')).toHaveTextContent(
      'read at 10:42:07, not live · 0 s ago'
    )
    expect(live.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(live).toHaveTextContent('Checked at every new order')
    expect(screen.getByTestId('daily-card')).toHaveTextContent('Limit ₹4,000 a day.')
    expect(screen.getByTestId('daily-card')).toHaveTextContent(READ_NOTE)
  })

  it('counts the age of the figure up with the clock, reading nothing again', () => {
    card(status(4000, { live: day() }))
    act(() => vi.advanceTimersByTime(12_000))
    expect(screen.getByTestId('daily-read-live')).toHaveTextContent('not live · 12 s ago')
    act(() => vi.advanceTimersByTime(3 * 60_000))
    expect(screen.getByTestId('daily-read-live')).toHaveTextContent('not live · 3 min ago')
  })

  it('says a profit, a flat day and an approximate figure in words too', () => {
    card(
      status(4000, {
        live: day({ figure: 1200, approximate: true }),
        sandbox: day({ figure: 0 }),
      })
    )
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent('+₹1,200.00profit today')
    expect(live).toHaveTextContent(' · approximate')
    const sandbox = screen.getByRole('region', { name: 'Sandbox today' })
    expect(sandbox).toHaveTextContent('₹0.00today')
    expect(sandbox).not.toHaveTextContent('+')
  })

  it('says when the day has locked, and when the next new order will lock it', () => {
    card(
      status(4000, {
        live: day({
          figure: -4100,
          reached: true,
          lock: { at: READ_AT, figure: -4100, limit: 4000 },
        }),
        sandbox: day({ figure: -4000, reached: true }),
      })
    )
    expect(screen.getByRole('region', { name: 'Live today' })).toHaveTextContent(
      'Locked at 10:42: new orders refused until tomorrow'
    )
    expect(screen.getByRole('region', { name: 'Sandbox today' })).toHaveTextContent(
      'Reached: the next new order locks the day'
    )
  })

  it('outside the session says when it is checked again, never "Reached"', () => {
    card(
      status(4000, {
        live: day({
          figure: -6000,
          in_session: false,
          opens_at: '2026-10-28T09:15:00+05:30',
          read_at: '2026-10-28T02:00:00+00:00', // 07:30 in India, the same day
        }),
        sandbox: day({ in_session: false, opens_at: '2026-10-29T09:15:00+05:30' }),
      })
    )
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent('Market closed: checked again from 09:15')
    expect(live).not.toHaveTextContent('Reached')
    const sandbox = screen.getByRole('region', { name: 'Sandbox today' })
    expect(sandbox).toHaveTextContent('Market closed: checked again from 09:15 on ')
    expect(sandbox).toHaveTextContent('29 Oct')
  })

  it('says delivery (CNC) is not counted, with a limit and without one', () => {
    const { unmount } = card(status(4000, { live: day() }))
    expect(screen.getByTestId('daily-card')).toHaveTextContent('Delivery (CNC) is not counted.')
    unmount()
    card(status(null, { live: day() }))
    expect(screen.getByTestId('daily-card')).toHaveTextContent(DELIVERY_NOT_COUNTED)
  })

  it('suggests how to pick a limit when there is none, and never picks one', () => {
    card(status(null, { live: day() }))
    const box = screen.getByTestId('daily-card')
    expect(box).toHaveTextContent(DAILY_SUGGESTION)
    expect(within(box).getByRole('region', { name: 'Live today' })).toHaveTextContent('Off')
  })

  it('says settings that could not be read could not be read, never "Off"', () => {
    card(status(null, { live: day() }, false))
    const box = screen.getByTestId('daily-card')
    expect(box).toHaveTextContent(LIMIT_UNREADABLE)
    expect(within(box).getByRole('region', { name: 'Live today' })).not.toHaveTextContent('Off')
    expect(box).not.toHaveTextContent(DAILY_SUGGESTION)
  })

  it('never shows an old figure as current: one that could not be read says so', () => {
    card(status(4000, { live: day({ figure: null, unchecked: true }) }))
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent("Today's figure could not be read just now.")
    expect(live).not.toHaveTextContent('₹')
    expect(live).toHaveTextContent('Not being checked just now: positions cannot be read.')
  })
})
