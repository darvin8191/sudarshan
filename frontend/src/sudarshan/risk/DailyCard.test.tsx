/**
 * The daily loss card (M5.14): today's figure with its sign, an arrow and a word as well as the
 * colour; when it was read; the limit, or a suggestion when there is none (R4: the owner picks
 * the figure); the day's lock; and a figure that could not be read, never a number passed off as
 * current.
 */

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DailyStatus, RiskStatus } from '@/api/risk'
import { DAILY_SUGGESTION, DailyCard } from './DailyCard'

const AS_OF = '2026-10-28T05:12:07+00:00' // 10:42:07 in India

function day(over: Partial<DailyStatus> = {}): DailyStatus {
  return {
    figure: -3250.5,
    approximate: false,
    as_of: AS_OF,
    reached: false,
    lock: null,
    unchecked: false,
    ...over,
  }
}

function status(limit: number | null, daily: RiskStatus['daily']): RiskStatus {
  return {
    stage: 'on',
    kill_switch: { known: true, on: false },
    settings: { daily_loss_limit: limit } as RiskStatus['settings'],
    gate: { orders: 0, p50_ms: null, p95_ms: null, max_ms: null, last_problem: null },
    upstox_kill_switch: null,
    broker: 'upstox',
    daily,
  }
}

describe('the daily loss card', () => {
  it("shows today's figure with its sign, an arrow and a word, and when it was read", () => {
    render(<DailyCard status={status(4000, { live: day() })} />)
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent('−₹3,250.50loss today')
    expect(live).toHaveTextContent('as of 28 Oct, 10:42:07 IST')
    expect(live.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(live).toHaveTextContent('Checked at every new order')
    expect(screen.getByTestId('daily-card')).toHaveTextContent('Limit ₹4,000 a day.')
  })

  it('says a profit, a flat day and an approximate figure in words too', () => {
    render(
      <DailyCard
        status={status(4000, {
          live: day({ figure: 1200, approximate: true }),
          sandbox: day({ figure: 0 }),
        })}
      />
    )
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent('+₹1,200.00profit today')
    expect(live).toHaveTextContent(', approximate')
    const sandbox = screen.getByRole('region', { name: 'Sandbox today' })
    expect(sandbox).toHaveTextContent('₹0.00today')
    expect(sandbox).not.toHaveTextContent('+')
  })

  it('says when the day has locked, and when the next new order will lock it', () => {
    render(
      <DailyCard
        status={status(4000, {
          live: day({
            figure: -4100,
            reached: true,
            lock: { at: AS_OF, figure: -4100, limit: 4000 },
          }),
          sandbox: day({ figure: -4000, reached: true }),
        })}
      />
    )
    expect(screen.getByRole('region', { name: 'Live today' })).toHaveTextContent(
      'Locked at 10:42: new orders refused until tomorrow'
    )
    expect(screen.getByRole('region', { name: 'Sandbox today' })).toHaveTextContent(
      'Reached: the next new order locks the day'
    )
  })

  it('suggests how to pick a limit when there is none, and never picks one', () => {
    render(<DailyCard status={status(null, { live: day() })} />)
    const card = screen.getByTestId('daily-card')
    expect(card).toHaveTextContent(DAILY_SUGGESTION)
    expect(within(card).getByRole('region', { name: 'Live today' })).toHaveTextContent('Off')
  })

  it('never shows an old figure as current: one that could not be read says so', () => {
    render(<DailyCard status={status(4000, { live: day({ figure: null, unchecked: true }) })} />)
    const live = screen.getByRole('region', { name: 'Live today' })
    expect(live).toHaveTextContent("Today's figure could not be read just now.")
    expect(live).not.toHaveTextContent('₹')
    expect(live).toHaveTextContent('Not being checked just now: positions cannot be read.')
  })
})
