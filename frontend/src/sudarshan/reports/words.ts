import type { Report, ReportKind, Settings } from '../journal/types'
import { REPORT_TITLES } from '../journal/types'

/**
 * Two things the contract's Report and Settings do not carry yet, read when the bridge sends
 * them: why a report was not sent, whether the alerts service runs, and the rate card in use.
 * Without them the page says what it knows and no more.
 */
export interface ReportExtra {
  reason?: string | null
}
export interface SettingsExtra {
  alerts_on?: boolean
  rate_card?: { version?: string | null; from_day?: number | null } | null
}

export type ReportRow = Report & ReportExtra
export type SettingsRead = Settings & SettingsExtra

export const ALERTS_OFF = 'the alerts service is switched off on the server'

export const KIND_ORDER: ReportKind[] = ['eod', 'brief', 'weekly', 'monthly', 'fy']

/** The report switches with the time each is built at, and the rule the time follows. */
export const SWITCHES: ReadonlyArray<{
  kind: ReportKind
  key: 'eod_on' | 'brief_on' | 'weekly_on' | 'monthly_on' | 'fy_on'
  time: string
  rule: string
}> = [
  {
    kind: 'eod',
    key: 'eod_on',
    time: '15:45',
    rule: '5 minutes after the F&O close; a special day follows its own close',
  },
  {
    kind: 'brief',
    key: 'brief_on',
    time: '08:45',
    rule: '30 minutes before the first session; a special day follows its own session',
  },
  { kind: 'weekly', key: 'weekly_on', time: 'Sunday 18:00', rule: 'every week' },
  {
    kind: 'monthly',
    key: 'monthly_on',
    time: '1st of the month 07:00',
    rule: 'for the month just ended',
  },
  {
    kind: 'fy',
    key: 'fy_on',
    time: '1 April 07:00',
    rule: 'for the year just ended; on request any time',
  },
]

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

/** SUDARSHAN_<Kind>_<period>.pdf, ASCII only; the extension is the file's own when it has one. */
export function downloadName(report: Report): string {
  const own = report.file_name?.match(/\.([A-Za-z0-9]{2,4})$/)?.[1]?.toLowerCase()
  const ext = own ?? (report.file_mime === 'text/csv' ? 'csv' : 'pdf')
  return `SUDARSHAN_${ascii(kindTitle(report.kind))}_${ascii(report.period_key)}.${ext}`
}

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
