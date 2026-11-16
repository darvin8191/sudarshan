// SUDARSHAN M13.4-M13.9 (P8 U1): the TRINETRA view's words on the phone. Every sentence is a
// template of the contract (WORDS, AGE_WORDS and the rest in types.ts, generated from
// sudarshan/signals/contract.py); this file fills them by the README's rules (section 4, "Times in
// words", and section 5), so a header worked out again on the phone reads as the service's own.
// Pure: nothing here reads a clock, the network or the page.
import { groupedNumber, type NumberText } from '@/sudarshan/dashboard/format'
import {
  AGE_WORDS,
  BIAS_WORDS,
  type Bias,
  COLOUR_FOR_MS,
  type Freshness,
  type FreshStatus,
  IST_OFFSET_S,
  LANG_FALLBACK,
  LANG_NAMES,
  type Lang,
  type Levels,
  MODULE_NAMES,
  MONTHS,
  OTHER_MODULE_NAME,
  type Why,
  WORDS,
} from './types'

const IST_MS = IST_OFFSET_S * 1000
const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** `{name}` fields filled; a field the template does not name is left out, one it names but is not given stays. */
export function fill(template: string, fields: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in fields ? String(fields[name]) : whole
  )
}

/** The parts of an instant in India. */
function ist(ms: number) {
  const at = new Date(ms + IST_MS)
  return {
    year: at.getUTCFullYear(),
    month: at.getUTCMonth(),
    day: at.getUTCDate(),
    hour: at.getUTCHours(),
    minute: at.getUTCMinutes(),
    second: at.getUTCSeconds(),
  }
}

const two = (n: number) => String(n).padStart(2, '0')

/** The trading day in India, YYYYMMDD (the contract's day). */
export function istDayOf(ms: number): number {
  const { year, month, day } = ist(ms)
  return year * 10_000 + (month + 1) * 100 + day
}

/** The next midnight in India after `ms`. */
export function nextIstMidnight(ms: number): number {
  return Math.floor((ms + IST_MS) / DAY) * DAY + DAY - IST_MS
}

/** An instant at an India wall time on the same India day as `ms`: istAt(now, 9, 15). */
export function istAt(ms: number, hour: number, minute: number): number {
  return Math.floor((ms + IST_MS) / DAY) * DAY - IST_MS + hour * HOUR + minute * MINUTE
}

/** "11 Nov": a day as the words name it. */
export function dayWords(ms: number): string {
  const { day, month } = ist(ms)
  return `${day} ${MONTHS[month]}`
}

/**
 * A time in words (README section 4): HH:MM, or HH:MM:SS with `seconds`, in India, with "14 Nov "
 * in front when it is not on the same India day as `now`.
 */
export function timeWords(ms: number, now: number, seconds = false): string {
  const { hour, minute, second } = ist(ms)
  const clock = seconds
    ? `${two(hour)}:${two(minute)}:${two(second)}`
    : `${two(hour)}:${two(minute)}`
  return istDayOf(ms) === istDayOf(now) ? clock : `${dayWords(ms)} ${clock}`
}

/**
 * An age or a difference of clocks: "under a minute", "1 minute", "N minutes" under 120 minutes,
 * "N hours" under 48 hours, else "N days" (whole units, rounded down).
 */
export function ageWords(ms: number): string {
  const age = Math.max(0, ms)
  if (age < MINUTE) return AGE_WORDS.under_minute
  const minutes = Math.floor(age / MINUTE)
  if (minutes < 120)
    return minutes === 1 ? AGE_WORDS.minute : fill(AGE_WORDS.minutes, { n: minutes })
  const hours = Math.floor(age / HOUR)
  if (hours < 48) return fill(AGE_WORDS.hours, { n: hours })
  return fill(AGE_WORDS.days, { n: Math.floor(age / DAY) })
}

/** The age at which `ageWords` next says something else. */
export function nextAgeStep(ms: number): number {
  const age = Math.max(0, ms)
  if (age < MINUTE) return MINUTE
  const minutes = Math.floor(age / MINUTE)
  if (minutes < 120) return (minutes + 1) * MINUTE
  const hours = Math.floor(age / HOUR)
  if (hours < 48) return (hours + 1) * HOUR
  return (Math.floor(age / DAY) + 1) * DAY
}

/** The header line as it stands at `now` (the server's clock). */
export interface FreshNow {
  status: FreshStatus
  /** Section 3.6's sentence, worked out again for `now`. */
  words: string
  /** How old the last reading is, in words; null when nothing was ever received. */
  age: string | null
  /**
   * A colour of bias may be shown: while fresh, and once stale until 30 minutes of age (the
   * service's rule, words.py); never when nothing was ever heard.
   */
  colour: boolean
  /** When this answer next changes by the clock alone (server clock); null: not by the clock. */
  nextMs: number | null
}

const STATUSES = new Set<string>(['fresh', 'stale', 'stopped', 'never'])

/**
 * The freshness the service answered, carried on to `now` by the clock alone (README section 4,
 * and the service's words.py, whose vectors this is tested against): fresh until
 * `fresh_until_ms`, then stale; the age moves on; a stale or stopped reading loses its colour of
 * bias at 30 minutes of age; at midnight in India a time gains its day. A status this release does
 * not know keeps the service's own sentence and colour.
 */
export function freshnessAt(freshness: Freshness, now: number): FreshNow {
  const published = freshness.published_ms
  let status: FreshStatus = freshness.status
  if (status === 'fresh' && freshness.fresh_until_ms !== null && now >= freshness.fresh_until_ms) {
    status = 'stale'
  }
  const timed = published !== null && status !== 'never' && STATUSES.has(status)
  const ageMs = timed ? Math.max(0, now - published) : null
  let words: string
  if (!STATUSES.has(status)) words = freshness.words
  else if (status === 'never' || published === null || ageMs === null) {
    words = status === 'never' ? WORDS.never : freshness.words
  } else if (status === 'fresh') {
    words = fill(WORDS.fresh, { time: timeWords(published, now, true) })
  } else if (status === 'stale') {
    words = fill(WORDS.stale, { time: timeWords(published, now), age: ageWords(ageMs) })
  } else {
    words = fill(WORDS.stopped, { time: timeWords(published, now) })
  }
  const colour = !STATUSES.has(status)
    ? freshness.colour
    : ageMs !== null && (status === 'fresh' || ageMs < COLOUR_FOR_MS)
  const moments: number[] = []
  if (published !== null && ageMs !== null) {
    moments.push(published + nextAgeStep(ageMs), nextIstMidnight(now))
    if (colour && status !== 'fresh') moments.push(published + COLOUR_FOR_MS)
  }
  if (status === 'fresh' && freshness.fresh_until_ms !== null)
    moments.push(freshness.fresh_until_ms)
  const ahead = moments.filter((at) => at > now)
  return {
    status,
    words,
    age: ageMs === null ? null : ageWords(ageMs),
    colour,
    nextMs: ahead.length > 0 ? Math.min(...ahead) : null,
  }
}

/**
 * The header with the age of the last reading always in it: the fresh sentence gains "· 3 minutes
 * old", the stopped one "Its last reading is 3 hours old."; the stale one says its age already.
 */
export function freshLine(fresh: FreshNow): string {
  if (fresh.age === null) return fresh.words
  if (fresh.status === 'fresh') return `${fresh.words} · ${fresh.age} old`
  if (fresh.status === 'stopped') return `${fresh.words} Its last reading is ${fresh.age} old.`
  return fresh.words
}

/** An age where a line has little room (the chart's chip): "<1 min", "14 min", "3 h", "2 d". */
export function shortAge(ms: number): string {
  const age = Math.max(0, ms)
  if (age < MINUTE) return '<1 min'
  const minutes = Math.floor(age / MINUTE)
  if (minutes < 120) return `${minutes} min`
  const hours = Math.floor(age / HOUR)
  if (hours < 48) return `${hours} h`
  return `${Math.floor(age / DAY)} d`
}

/** What a screen calls a module: its TRINETRA name, or "Other" for one this release does not name. */
export function moduleName(module: string): string {
  return (MODULE_NAMES as Record<string, string>)[module] ?? OTHER_MODULE_NAME
}

/** The bias in its word: bullish, bearish or neutral. */
export function biasWord(bias: Bias): string {
  return BIAS_WORDS[bias] ?? BIAS_WORDS.neutral
}

/** A confidence as a figure: 0.71. */
export function confidenceWords(confidence: number | null): string | null {
  if (confidence === null || !Number.isFinite(confidence)) return null
  return Math.min(1, Math.max(0, confidence)).toFixed(2)
}

/** "since 11 Nov" (another day) or "since 10:42" (today), and "3 sessions". */
export function sinceWords(since: number | null, sessions: number | null, now: number): string {
  const parts: string[] = []
  if (since !== null) {
    parts.push(
      istDayOf(since) === istDayOf(now)
        ? `since ${timeWords(since, now)}`
        : `since ${dayWords(since)}`
    )
  }
  if (sessions !== null && sessions > 0)
    parts.push(sessions === 1 ? '1 session' : `${sessions} sessions`)
  const line = parts.join(', ')
  return line ? line.charAt(0).toUpperCase() + line.slice(1) : ''
}

const LEVEL_NAMES: ReadonlyArray<[keyof Levels, string]> = [
  ['trigger', 'Trigger'],
  ['invalid', 'Invalid at'],
  ['target', 'Target'],
]

const priceFormats = new Map<number, NumberText>()
/** An index level in Indian grouping: 25,900 or 25,912.35. */
export function levelWords(value: number): string {
  const decimals = Number.isInteger(value) ? 0 : 2
  let format = priceFormats.get(decimals)
  if (!format) {
    format = groupedNumber({
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    priceFormats.set(decimals, format)
  }
  return format.format(value)
}

/** The levels a reading carries, each with its name: [["Trigger", "25,900"], ...]. */
export function levelRows(levels: Levels | null): Array<[string, string]> {
  if (!levels) return []
  return LEVEL_NAMES.flatMap(([key, name]) => {
    const value = levels[key]
    return typeof value === 'number' && Number.isFinite(value) ? [[name, levelWords(value)]] : []
  }) as Array<[string, string]>
}

/** TRINETRA's explanation in the owner's language, or the first it sent, said as such. */
export interface WhyShown {
  text: string | null
  lang: Lang | null
  /** "TRINETRA did not write this in Gujarati; shown in English.", or that none was sent. */
  note: string | null
}

export function whyIn(why: Why | null | undefined, lang: Lang): WhyShown {
  const said = (key: Lang) => {
    const text = why?.[key]
    return typeof text === 'string' && text.trim() !== '' ? text : null
  }
  const own = said(lang)
  if (own !== null) return { text: own, lang, note: null }
  for (const other of LANG_FALLBACK) {
    const text = said(other)
    if (text !== null) {
      return {
        text,
        lang: other,
        note: fill(WORDS.why_fallback, { wanted: LANG_NAMES[lang], shown: LANG_NAMES[other] }),
      }
    }
  }
  return { text: null, lang: null, note: WORDS.why_none }
}
