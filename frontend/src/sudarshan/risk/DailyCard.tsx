import { CalendarClock, Lock, Minus, TrendingDown, TrendingUp, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { DailyStatus, RiskStatus } from '@/api/risk'
import { clockOf } from './parts'

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
const secondsClock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})
const dayOfWeek = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})
const indiaDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' })

const PIPES: Record<string, string> = { live: 'Live', sandbox: 'Sandbox' }

/** R4: the owner picks the figure; the card only suggests how to think of one. */
export const DAILY_SUGGESTION =
  'A common choice is about 2% of the money you trade F&O with, for example ₹4,000 on ₹2,00,000.'

/** The owner's decision: shares bought to hold are not in the day's figure (daily.py). */
export const DELIVERY_NOT_COUNTED = 'Delivery (CNC) is not counted.'

/** Limits review L3: the figure is a snapshot of a book Upstox itself refreshes slowly. */
export const READ_NOTE =
  'Upstox updates positions about once a minute; "read at" is when SUDARSHAN read them.'

export const LIMIT_UNREADABLE = 'Your daily limit could not be read just now.'

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

/** "12 s ago", "3 min ago", "2 h ago". */
export function ageOf(seconds: number): string {
  if (seconds < 60) return `${seconds} s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  return `${Math.floor(seconds / 3600)} h ago`
}

/**
 * Seconds since the status arrived (the phone's own clock on both sides, so a phone set a
 * little wrong still counts right), ticking once a second while the card is shown. Only the
 * words move: nothing is read again.
 */
function useAge(receivedAt: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(0, Math.floor((now - receivedAt) / 1000))
}

/** When the closed session opens again: "09:15", or "09:15 on Thu, 29 Oct" on another day. */
export function opensWords(opensAt: string | null, readAt: string): string {
  if (!opensAt) return 'when it opens'
  const opens = new Date(opensAt)
  if (Number.isNaN(opens.getTime())) return 'when it opens'
  const read = new Date(readAt)
  const sameDay =
    !Number.isNaN(read.getTime()) && indiaDate.format(opens) === indiaDate.format(read)
  const clock = clockOf(opensAt)
  return sameDay ? `from ${clock}` : `from ${clock} on ${dayOfWeek.format(opens)}`
}

function Chip({ children, icon }: { children: string; icon: typeof Lock }) {
  const Icon = icon
  return (
    <span className="sd-day-chip inline-flex min-h-8 items-center gap-1.5 rounded-full border px-3 text-[0.8125rem] font-bold">
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      {children}
    </span>
  )
}

function Quiet({ children }: { children: string }) {
  return <span className="text-[0.875rem] text-[color:var(--text-secondary)]">{children}</span>
}

function DayState({
  day,
  limit,
  limitRead,
}: {
  day: DailyStatus
  limit: number | null
  limitRead: boolean
}) {
  if (day.lock) {
    return (
      <Chip
        icon={Lock}
      >{`Locked at ${clockOf(day.lock.at)}: new orders refused until tomorrow`}</Chip>
    )
  }
  if (!limitRead) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.875rem]">
        <TriangleAlert
          aria-hidden="true"
          className="size-4 shrink-0 text-[color:var(--warning-text)]"
        />
        {LIMIT_UNREADABLE}
      </span>
    )
  }
  if (limit === null) return <Quiet>Off</Quiet>
  if (!day.in_session) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[0.875rem] text-[color:var(--text-secondary)]">
        <CalendarClock aria-hidden="true" className="size-4 shrink-0" />
        {`Market closed: checked again ${opensWords(day.opens_at, day.read_at)}`}
      </span>
    )
  }
  if (day.reached) return <Chip icon={Lock}>Reached: the next new order locks the day</Chip>
  return <Quiet>Checked at every new order</Quiet>
}

function DaySection({
  pipe,
  day,
  limit,
  limitRead,
  age,
}: {
  pipe: string
  day: DailyStatus
  limit: number | null
  limitRead: boolean
  age: number
}) {
  const read = new Date(day.read_at)
  const readClock = Number.isNaN(read.getTime()) ? '' : secondsClock.format(read)
  return (
    <section
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
        <div className="flex flex-col gap-0.5">
          <DayFigure value={day.figure} />
          <p
            className="text-[0.78125rem] text-[color:var(--text-tertiary)]"
            data-testid={`daily-read-${pipe}`}
          >
            {`read at ${readClock}, not live · ${ageOf(age)}`}
            {day.approximate ? ' · approximate' : ''}
          </p>
        </div>
      )}
      <DayState day={day} limit={limit} limitRead={limitRead} />
      {day.unchecked && (
        <p className="flex items-start gap-2 text-[0.875rem]">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0 text-[color:var(--warning-text)]"
          />
          Not being checked just now: positions cannot be read. Orders go meanwhile, since any of
          them may be an exit.
        </p>
      )}
    </section>
  )
}

/**
 * The daily loss card (M5.14): today's figure per pipe the guard covers, the limit, the lock.
 *
 * The figure is a snapshot read when the page asked (rule 7: never shown as live), so it says
 * when it was read and how long ago, the age moving with the clock. Outside the F&O session no
 * order looks at it, so the card says when it is checked again instead of "Reached". Settings
 * that could not be read are said to be unreadable, never "Off".
 */
export function DailyCard({
  status,
  receivedAt,
}: {
  status: RiskStatus
  /** When the status arrived, by the phone's clock (TanStack Query's dataUpdatedAt). */
  receivedAt: number
}) {
  const limitRead = status.settings !== null && status.settings !== undefined
  const limit = status.settings?.daily_loss_limit ?? null
  const days = Object.entries(status.daily ?? {})
  const age = useAge(receivedAt)
  let lead: string
  if (!limitRead) {
    lead = `${LIMIT_UNREADABLE} New orders are refused for the rest of the day once the day's loss reaches the limit; exits and cancels always go.`
  } else if (limit === null) {
    lead = `Off. When it is on, new orders are refused for the rest of the day once the day's loss reaches it; exits and cancels always go. ${DELIVERY_NOT_COUNTED} ${DAILY_SUGGESTION}`
  } else {
    lead = `Limit ${wholeRupees.format(limit)} a day. Once the day's loss reaches it, new orders are refused until tomorrow; exits and cancels always go. ${DELIVERY_NOT_COUNTED}`
  }
  return (
    <div className="flex flex-col gap-3" data-testid="daily-card">
      <p className="text-[0.9375rem] leading-snug">{lead}</p>
      {days.map(([pipe, day]) => (
        <DaySection
          key={pipe}
          pipe={pipe}
          day={day}
          limit={limit}
          limitRead={limitRead}
          age={age}
        />
      ))}
      {days.length > 0 && (
        <p className="text-[0.78125rem] text-[color:var(--text-tertiary)]">{READ_NOTE}</p>
      )}
    </div>
  )
}
