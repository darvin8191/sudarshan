// SUDARSHAN P7 (M7.34): what a goal's card says, worked out on the phone when its value moves.
// The service works out the amount to add each month once, at its read (core/goals.py, Decimal).
// A goal that counts tabs moves with their ticks, so the amount is worked out again here from the
// value now, by the same formula; with the value the service read, its own figure is used as it
// is. vectors/goals.json holds both to the same answers (sums.test.ts).
import { formatRupees } from '@/sudarshan/dashboard/format'
import { monthOf, rupeesText } from '../tab/format'
import type { Goal } from '../types'

const BP = 10_000
const PAISE = 100

/**
 * What to add at each month's end to reach the target, in paise (whole rupees, rounded up); 0 when
 * what is there already grows to the target; null when it cannot be worked out as a number.
 * `months` below 1 counts as 1, as on the service.
 */
export function requiredMonthly(
  targetPaise: number,
  valuePaise: number,
  months: number,
  rateBp: number
): number | null {
  const left = Math.max(1, months)
  const monthly = rateBp ? (1 + rateBp / BP) ** (1 / 12) - 1 : 0
  const growth = (1 + monthly) ** left
  const need = targetPaise - valuePaise * growth
  if (!Number.isFinite(need)) return null
  if (need <= 0) return 0
  const each = (monthly ? (need * monthly) / (growth - 1) : need / left) / PAISE
  if (!Number.isFinite(each)) return null
  // A whole number of rupees is not pushed a rupee higher by the last digits of a float.
  const near = Math.round(each)
  const rupees = Math.abs(each - near) <= Math.max(1e-6, near * 1e-12) ? near : Math.ceil(each)
  const paise = rupees * PAISE
  return Number.isSafeInteger(paise) ? paise : null
}

export interface GoalFigures {
  required_monthly_paise: number
  on_track: boolean
}

/** The amount to add and whether it is on track, for the value the card shows now. */
export function figuresNow(goal: Goal, valuePaise: number): GoalFigures {
  const read = { required_monthly_paise: goal.required_monthly_paise, on_track: goal.on_track }
  if (valuePaise === goal.value_now_paise || goal.problem !== null) return read
  const amount = requiredMonthly(goal.target_paise, valuePaise, goal.months_left, goal.rate_bp)
  return amount === null ? read : { required_monthly_paise: amount, on_track: amount === 0 }
}

/**
 * The percentage a card prints, one decimal: cut, never rounded up, below 100, so "100.0%" is
 * never printed above an amount still to add. From the service's four-decimal text, by its digits.
 */
export function shownProgress(text: string | null | undefined): number {
  const value = Number(text)
  if (!Number.isFinite(value) || value <= 0) return 0
  if (value >= 100) return value
  const [whole, fraction = ''] = String(text).split('.')
  return Number(`${whole}.${fraction.charAt(0) || '0'}`)
}

/** Whether the goal's day is before `today` (both YYYYMMDD). */
export const overdue = (goal: Goal, today: number): boolean => goal.target_day < today

/** "By March 2031 · 52 months left"; a goal past its day says so, never "0 months left". */
export function byWords(goal: Goal, today: number): string {
  const month = monthOf(goal.target_day).label
  if (overdue(goal, today)) return `Was due by ${month} · its date has passed`
  if (goal.months_left === 0) return `By ${month} · less than a month left`
  const left = goal.months_left === 1 ? '1 month left' : `${goal.months_left} months left`
  return `By ${month} · ${left}`
}

const wholeRupees = (paise: number): string => formatRupees(paise / PAISE, { decimals: 0 })

/**
 * What to add at the owner's assumed return, or that nothing more is needed. A goal past its day
 * says what is still missing and what to do; one due within a month asks for it by the date. Never
 * an amount "a month" when no whole month is left.
 */
export function addWords(goal: Goal, valuePaise: number, today: number): string {
  const short = goal.target_paise - valuePaise
  if (overdue(goal, today)) {
    if (short <= 0) return 'Target reached'
    return `${rupeesText(short)} is still to be added. Open the goal to choose a new date.`
  }
  const now = figuresNow(goal, valuePaise)
  if (now.on_track) return 'On track without adding more'
  const rate = `at ${goal.rate_bp / 100}% a year (your assumption)`
  const amount = wholeRupees(now.required_monthly_paise)
  if (goal.months_left === 0) return `Add about ${amount} by the date ${rate}`
  return `Add about ${amount} a month ${rate}`
}

/** A deleted tab on a card: it counts as nothing, and the form unlinks it. */
export function goneWords(count: number): string | null {
  if (count <= 0) return null
  return count === 1
    ? 'A tab this goal counted was deleted and counts as nothing now. Open the goal to unlink it.'
    : `${count} tabs this goal counted were deleted and count as nothing now. Open the goal to unlink them.`
}

/** An archived tab on a card: it still counts what it holds. */
export function archivedWords(names: readonly string[]): string | null {
  if (names.length === 0) return null
  const list = names.join(', ')
  return names.length === 1
    ? `Counts ${list}, a tab that is archived. It still counts what it holds.`
    : `Counts ${list}, tabs that are archived. They still count what they hold.`
}
