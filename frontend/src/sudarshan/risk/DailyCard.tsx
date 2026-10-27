import { Lock, Minus, TrendingDown, TrendingUp, TriangleAlert } from 'lucide-react'
import type { DailyStatus, RiskStatus } from '@/api/risk'
import { clockOf, stampOf } from './parts'

const rupees = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const wholeRupees = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

const PIPES: Record<string, string> = { live: 'Live', sandbox: 'Sandbox' }

/** R4: the owner picks the figure; the card only suggests how to think of one. */
export const DAILY_SUGGESTION =
  'A common choice is about 2% of the money you trade F&O with, for example ₹4,000 on ₹2,00,000.'

const FIGURE_LOOK = {
  loss: { Icon: TrendingDown, sign: '−', word: 'loss today', ink: 'text-[color:var(--down)]' },
  profit: { Icon: TrendingUp, sign: '+', word: 'profit today', ink: 'text-[color:var(--up)]' },
  flat: { Icon: Minus, sign: '', word: 'today', ink: 'text-[color:var(--text-primary)]' },
} as const

/** Today's P&L: the sign, an arrow and a word as well as the colour, never colour alone. */
function DayFigure({ value }: { value: number }) {
  const look = FIGURE_LOOK[value < 0 ? 'loss' : value > 0 ? 'profit' : 'flat']
  return (
    <span className={`inline-flex items-center gap-1.5 ${look.ink}`}>
      <look.Icon aria-hidden="true" className="size-5 shrink-0" />
      <span className="sd-display text-[1.75rem] leading-none font-bold">
        {look.sign}
        {rupees.format(Math.abs(value))}
      </span>
      <span className="text-[0.8125rem] font-semibold">{look.word}</span>
    </span>
  )
}

function DayState({ day, limit }: { day: DailyStatus; limit: number | null }) {
  if (day.lock) {
    return (
      <span className="sd-day-chip inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-bold">
        <Lock aria-hidden="true" className="size-4" />
        Locked at {clockOf(day.lock.at)}: new orders refused until tomorrow
      </span>
    )
  }
  if (limit === null) {
    return <span className="text-[0.875rem] text-[color:var(--text-secondary)]">Off</span>
  }
  if (day.reached) {
    return (
      <span className="sd-day-chip inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-bold">
        <Lock aria-hidden="true" className="size-4" />
        Reached: the next new order locks the day
      </span>
    )
  }
  return (
    <span className="text-[0.875rem] text-[color:var(--text-secondary)]">
      Checked at every new order
    </span>
  )
}

/** The daily loss card (M5.14): today's figure per pipe the guard covers, the limit, the lock. */
export function DailyCard({ status }: { status: RiskStatus }) {
  const limit = status.settings?.daily_loss_limit ?? null
  const days = Object.entries(status.daily ?? {})
  return (
    <div className="flex flex-col gap-3" data-testid="daily-card">
      <p className="text-[0.9375rem] leading-snug">
        {limit === null
          ? `Off. When it is on, new orders are refused for the rest of the day once the day's loss reaches it; exits and cancels always go. ${DAILY_SUGGESTION}`
          : `Limit ${wholeRupees.format(limit)} a day. Once the day's loss reaches it, new orders are refused until tomorrow; exits and cancels always go.`}
      </p>
      {days.map(([pipe, day]) => (
        <section
          key={pipe}
          aria-label={`${PIPES[pipe] ?? pipe} today`}
          className="flex flex-col gap-1.5 border-t border-[color:var(--divider)] pt-3"
        >
          <h3 className="text-[0.875rem] font-semibold text-[color:var(--text-secondary)]">
            {PIPES[pipe] ?? pipe}
          </h3>
          {day.figure === null ? (
            <p className="text-[0.875rem] text-[color:var(--text-secondary)]">
              Today's figure could not be read just now.
            </p>
          ) : (
            <p className="flex flex-wrap items-baseline gap-x-2">
              <DayFigure value={day.figure} />
              <span className="text-[0.78125rem] text-[color:var(--text-tertiary)]">
                as of {stampOf(day.as_of)}
                {day.approximate ? ', approximate' : ''}
              </span>
            </p>
          )}
          <DayState day={day} limit={limit} />
          {day.unchecked && (
            <p className="flex items-start gap-2 text-[0.875rem]">
              <TriangleAlert
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
              />
              Not being checked just now: positions cannot be read. Orders go meanwhile, since any
              of them may be an exit.
            </p>
          )}
        </section>
      ))}
    </div>
  )
}
