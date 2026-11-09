/** Days as YYYYMMDD (IST) and the few calendar sums the freshness words need (core/days.py). */

const MS_PER_DAY = 86_400_000
const IST_OFFSET_MS = 19_800_000
/** Python's `date(1970, 1, 1).toordinal()`: the formulas below count from the year 1. */
const EPOCH_ORDINAL = 719_163

export function isDay(value: unknown): value is number {
  if (typeof value !== 'number' || !Number.isInteger(value)) return false
  if (value < 19_900_101 || value > 22_001_231) return false
  const [year, month, day] = parts(value)
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

function parts(day: number): [number, number, number] {
  return [Math.floor(day / 10_000), Math.floor(day / 100) % 100, day % 100]
}

/** Days since 1970-01-01. */
export function epochDay(day: number): number {
  const [year, month, number] = parts(day)
  return Math.round(Date.UTC(year, month - 1, number) / MS_PER_DAY)
}

export function daysBetween(start: number, end: number): number {
  return epochDay(end) - epochDay(start)
}

/** Monday to Friday; the exchange's holidays are for the caller to say. */
export function isWeekday(day: number): boolean {
  // 1 January 1970 was a Thursday: with Monday as 0, epoch day n is (n + 3) mod 7.
  return (((epochDay(day) + 3) % 7) + 7) % 7 < 5
}

function weekdaysThrough(ordinal: number): number {
  return Math.floor(ordinal / 7) * 5 + Math.min(ordinal % 7, 5)
}

/** Weekdays after `start` up to and including `end`, less any of `holidays` among them. */
export function businessDaysBetween(start: number, end: number, holidays: readonly number[] = []) {
  const first = epochDay(start) + EPOCH_ORDINAL
  const last = epochDay(end) + EPOCH_ORDINAL
  if (last <= first) return 0
  const count = weekdaysThrough(last) - weekdaysThrough(first)
  const shut = new Set(
    holidays.filter((day) => isDay(day) && start < day && day <= end && isWeekday(day))
  )
  return count - shut.size
}

/** The IST day an instant (epoch milliseconds) falls on. */
export function dayOfInstant(epochMs: number): number {
  const days = Math.floor((epochMs + IST_OFFSET_MS) / MS_PER_DAY)
  const date = new Date(days * MS_PER_DAY)
  return date.getUTCFullYear() * 10_000 + (date.getUTCMonth() + 1) * 100 + date.getUTCDate()
}

/** The IST time of day of an instant as HH:MM:SS; the milliseconds are dropped. */
export function clockOfInstant(epochMs: number): string {
  const seconds = Math.floor(
    ((((epochMs + IST_OFFSET_MS) % MS_PER_DAY) + MS_PER_DAY) % MS_PER_DAY) / 1000
  )
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(Math.floor(seconds / 3600))}:${two(Math.floor(seconds / 60) % 60)}:${two(seconds % 60)}`
}

const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]

/**
 * The IST date and time of an instant, "31 Mar 2026, 11:00:00". A figure kept for months must say
 * which day it was read, so a time of day alone is not used where a read can be old.
 */
export function dateTimeOfInstant(epochMs: number): string {
  const day = dayOfInstant(epochMs)
  const month = MONTH_ABBR[(Math.floor(day / 100) % 100) - 1] ?? '?'
  return `${day % 100} ${month} ${Math.floor(day / 10_000)}, ${clockOfInstant(epochMs)}`
}
