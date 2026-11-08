import type { AlertSettings, Grace, ScreenSpec } from './types'

/** The limits the service holds these to (spec.py), in its words. */
export const LIMITS = {
  maxAdds: { low: 1, high: 250, words: 'The most to add in one check' },
  graceRuns: { low: 1, high: 50, words: 'The number of checks' },
  minMatches: { low: 1, high: 1000, words: 'The fewest matches to tell' },
  cooldown: { low: 0, high: 1000, words: 'The wait between alerts' },
  perSymbol: { low: 1, high: 50, words: 'The most alerts for one stock a day' },
} as const

export const LIST_NAME_MAX = 64

/** What the service fills in for a screen that never set its alerts. */
export const DEFAULT_ALERTS: AlertSettings = {
  on: true,
  min_matches: 1,
  unique_per_day: true,
  skip_zero: true,
  tell_exits: false,
  after_trigger: 'continue',
  cooldown_runs: 0,
  max_per_day_per_symbol: 3,
  channels: null,
  columns: ['ltp', 'change_pct'],
}

export const DEFAULT_GRACE: Grace = { mode: 'runs', n: 2 }

type Target = NonNullable<ScreenSpec['target']>

/** A target the first time it is switched on: a new list named after the screen. */
export function freshTarget(name: string): Target {
  return {
    list_id: null,
    create_name: `Screener: ${name.trim() || 'new screen'}`.slice(0, LIST_NAME_MAX),
    grace: DEFAULT_GRACE,
    max_adds_per_run: 10,
  }
}

/** The service's refusal for a whole number out of range, so the sheet says what the service would. */
export function wholeProblem(text: string, limit: { low: number; high: number; words: string }) {
  const n = Number(text)
  return text.trim() !== '' && Number.isInteger(n) && n >= limit.low && n <= limit.high
    ? null
    : `${limit.words} must be a whole number from ${limit.low} to ${limit.high}.`
}

export function graceWords(grace: Grace): string {
  if (grace.mode === 'never') return 'a miss is never forgiven'
  if (grace.mode === 'day_end') return 'a miss is forgiven for the day'
  const n = grace.n ?? DEFAULT_GRACE.n ?? 2
  return `a miss is forgiven for ${n} ${n === 1 ? 'check' : 'checks'}`
}

/** The one line under "Add matches to a watchlist". */
export function targetWords(target: ScreenSpec['target'], listName: string | null): string {
  if (!target) return 'Matches are not added to a watchlist.'
  const where = target.list_id !== null ? (listName ?? 'your list') : (target.create_name ?? '')
  return `Adds to ${where} · ${graceWords(target.grace)} · up to ${target.max_adds_per_run} a check`
}

const CHANNEL_WORDS = (channels: AlertSettings['channels']) =>
  channels === null
    ? 'your usual channels'
    : channels.telegram && channels.app
      ? 'Telegram and the app'
      : channels.telegram
        ? 'Telegram'
        : channels.app
          ? 'the app'
          : 'nowhere'

/** The one line under "Alerts". */
export function alertWords(alerts: AlertSettings | undefined): string {
  const have = alerts ?? DEFAULT_ALERTS
  if (!have.on) return 'Alerts are off.'
  return `Alerts on · ${have.min_matches}+ matches · ${CHANNEL_WORDS(have.channels)}`
}
