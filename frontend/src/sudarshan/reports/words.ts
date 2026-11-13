import type { Report, ReportKind, ReportTimes, Settings } from '../journal/types'
import { REPORT_TITLES } from '../journal/types'

/**
 * What a service older than this page may leave out of its answers, read when the bridge sends
 * it: why a report was not sent; whether the alerts deliver, the rate card in use and when each
 * report is built. Without one the page says what it knows and no more, never a guess.
 */
export interface ReportExtra {
  reason?: string | null
}
export interface SettingsExtra {
  alerts_on?: boolean
  rate_card?: { version?: string | null; from_day?: number | null } | null
  times?: Partial<ReportTimes> | null
}

export type ReportRow = Report & ReportExtra
export type SettingsRead = Omit<Settings, keyof SettingsExtra> & SettingsExtra

export const ALERTS_OFF = 'the alerts service is switched off on the server'

export const KIND_ORDER: ReportKind[] = ['eod', 'brief', 'weekly', 'monthly', 'fy']

/**
 * The report switches: the field of the server's `times` that says when each is built (the day
 * ones for today, or the next trading day), and the rule that time follows.
 */
export const SWITCHES: ReadonlyArray<{
  kind: ReportKind
  key: 'eod_on' | 'brief_on' | 'weekly_on' | 'monthly_on' | 'fy_on'
  at: 'eod' | 'brief' | 'weekly' | 'monthly' | 'fy'
  rule: string
}> = [
  {
    kind: 'eod',
    key: 'eod_on',
    at: 'eod',
    rule: '5 minutes after the F&O close; a special day follows its own close',
  },
  {
    kind: 'brief',
    key: 'brief_on',
    at: 'brief',
    rule: '30 minutes before the first session; a special day follows its own session',
  },
  { kind: 'weekly', key: 'weekly_on', at: 'weekly', rule: 'every week' },
  { kind: 'monthly', key: 'monthly_on', at: 'monthly', rule: 'for the month just ended' },
  { kind: 'fy', key: 'fy_on', at: 'fy', rule: 'for the year just ended; on request any time' },
]

/** Where the server gave no time, the page says so: it never prints a time of its own. */
export const TIME_NOT_SAID = 'The server has not said the time yet.'

/** The time the server gave for `at` ("15:45", "Sunday 18:00"), or null when it gave none. */
export function timeOf(times: SettingsExtra['times'], at: keyof ReportTimes): string | null {
  const value = times?.[at]
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

/** A rule as a sentence of its own, for when no time stands before it. */
export const ruleSentence = (rule: string): string => `${rule[0].toUpperCase()}${rule.slice(1)}.`

/** The close check, when the server says its time: the day's fills read again after the close. */
export function checkWords(times: SettingsExtra['times']): string | null {
  const at = timeOf(times, 'check')
  return at
    ? `Close check at ${at}: the day's fills are read again and the journal corrected.`
    : null
}

/** What an empty archive means: when the next Day report comes, as the server says. */
export function emptyWords(times: SettingsExtra['times']): string {
  const eod = timeOf(times, 'eod')
  const when = eod
    ? `The Day report is built at ${eod} on each trading day`
    : "The Day report is built after each trading day's close; the server has not said the time yet"
  return `No reports yet. ${when}. You can also build the tax pack or this month's statement above.`
}

export const kindTitle = (kind: ReportKind): string => REPORT_TITLES[kind]

/** The delivery of one report in words, and which glyph carries it: never colour alone. */
export function deliveryOf(
  report: ReportRow,
  alertsOn: boolean | undefined
): { tone: 'sent' | 'text' | 'not'; word: string; detail: string | null } {
  if (report.status === 'sent') return { tone: 'sent', word: 'Sent', detail: null }
  if (report.status === 'text_only') {
    return {
      tone: 'text',
      word: 'Text only',
      detail: 'The file did not go; open the report to read it here.',
    }
  }
  const reason = report.reason?.trim()
  if (report.status === 'not_sent') {
    if (alertsOn === false) return { tone: 'not', word: `Not sent: ${ALERTS_OFF}`, detail: null }
    return { tone: 'not', word: reason ? `Not sent: ${reason}` : 'Not sent', detail: null }
  }
  // Ready: built and waiting for its turn at the alerts service.
  if (alertsOn === false) return { tone: 'not', word: `Not sent: ${ALERTS_OFF}`, detail: null }
  return { tone: 'text', word: 'Ready to send', detail: null }
}

const ascii = (text: string): string => text.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

/**
 * SUDARSHAN_<Kind>_<period>.pdf, ASCII only; the extension is the file's own when it has one, and
 * .csv for a monthly statement's CSV (`format`).
 */
export function downloadName(report: Report, format?: 'csv'): string {
  const own = report.file_name?.match(/\.([A-Za-z0-9]{2,4})$/)?.[1]?.toLowerCase()
  const ext = format === 'csv' ? 'csv' : (own ?? (report.file_mime === 'text/csv' ? 'csv' : 'pdf'))
  return `SUDARSHAN_${ascii(kindTitle(report.kind))}_${ascii(report.period_key)}.${ext}`
}

/** Which reports also come as a CSV of their trades: the monthly statement (JB9). */
export const hasCsv = (report: Report): boolean => report.kind === 'monthly'

const BUILT = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** "9 Nov, 15:45": when a report was built, as the clock in India shows it. */
export const builtWords = (ms: number): string => BUILT.format(ms)

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

/** The period a key names, in words: 20261109, 2026W45, 202610, FY2627. A key not known is shown as it is. */
export function periodWords(key: string): string {
  let m = /^(\d{4})(\d{2})(\d{2})$/.exec(key)
  if (m) {
    const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
    return new Intl.DateTimeFormat('en-IN', {
      timeZone: 'UTC',
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
      .format(date)
      .replace(/,/g, '')
  }
  m = /^(\d{4})W(\d{2})$/.exec(key)
  if (m) return `Week ${Number(m[2])}, ${m[1]}`
  m = /^(\d{4})(\d{2})$/.exec(key)
  if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`
  m = /^FY(\d{2})(\d{2})$/.exec(key)
  if (m) return `FY 20${m[1]}-${m[2]}`
  return key
}
