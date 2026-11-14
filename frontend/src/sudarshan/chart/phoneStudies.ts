/**
 * Indicators on the phone chart (M4.2, C3): one saved set for every
 * instrument, as broker apps keep it, three studies at most and two of them in
 * a pane of their own, so a mid-range phone still opens every candle on time.
 * The design allowed five; the throttled cost run (C14, `e2e/sd-chart.spec.ts`)
 * failed there, so the cap is three until the owner's phone says otherwise.
 *
 * The set is a list of the engine's own study ids with their settings, kept
 * on the phone (`prefs.ts`). It is applied to each chart as it is built: the
 * engine computes the studies, the page draws one quiet legend line for each.
 * A study that cannot run on an instrument (open interest on an index, a
 * volume study where nothing trades) is skipped there with a line saying so,
 * never taken out of the set.
 *
 * This file holds no state of its own and loads no tier: the host imports the
 * indicator tier on first use and passes the engine's registry in.
 */

/** One study of the set: the engine's id and the settings the owner gave it. */
export interface StudyEntry {
  id: string
  settings: Record<string, unknown>
}

export const MAX_STUDIES = 3
export const MAX_STUDY_PANES = 2

const COUNT_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six']

/**
 * The volume pane's moving average. Drawn by the host on the volume pane the
 * chart already has, as the full terminal draws its own, so it opens no pane.
 */
export const VOLUME_MA = 'volume-ma'
export const VOLUME_MA_LENGTH = 20

/** Where a study draws: on the candles, in a pane of its own, or by the host. */
export type Placement = 'onchart' | 'pane' | 'host'

export interface CommonStudy {
  id: string
  label: string
  placement: Placement
  /** Offered for a contract that has open interest only. */
  derivativeOnly?: true
}

/** The studies a trader reaches for first, in the order the sheet lists them. */
export const COMMON_STUDIES: readonly CommonStudy[] = [
  { id: 'ema', label: 'EMA', placement: 'onchart' },
  { id: 'sma', label: 'SMA', placement: 'onchart' },
  { id: 'vwap', label: 'VWAP', placement: 'onchart' },
  { id: 'bollinger', label: 'Bollinger Bands', placement: 'onchart' },
  { id: 'supertrend', label: 'Supertrend', placement: 'onchart' },
  { id: 'rsi', label: 'RSI', placement: 'pane' },
  { id: 'macd', label: 'MACD', placement: 'pane' },
  { id: 'atr', label: 'ATR', placement: 'pane' },
  { id: 'adx', label: 'ADX', placement: 'pane' },
  { id: 'cpr', label: 'CPR with pivots', placement: 'onchart' },
  { id: VOLUME_MA, label: 'Volume moving average', placement: 'host' },
  { id: 'open-interest', label: 'Open interest', placement: 'pane', derivativeOnly: true },
]

/** What the engine's registry says of a study, as far as the set needs it. */
export interface StudyFacts {
  name: string
  placement: Placement
  category?: string
}

export type Refusal = 'count' | 'panes'

export const REFUSED: Record<Refusal, string> = {
  count: `${COUNT_WORDS[MAX_STUDIES]} indicators at most on the phone chart. Remove one, or open the full terminal.`,
  panes:
    'Two indicators with a pane of their own at most on the phone chart. Remove one of those, or open the full terminal.',
}

/** Why the set cannot take one more study that draws this way, or null when it can. */
export function refusal(
  set: readonly StudyEntry[],
  placement: Placement,
  placementOf: (id: string) => Placement
): Refusal | null {
  if (set.length >= MAX_STUDIES) return 'count'
  const panes = set.filter((entry) => placementOf(entry.id) === 'pane').length
  if (placement === 'pane' && panes >= MAX_STUDY_PANES) return 'panes'
  return null
}

/** Open interest is a contract's; an index and a share have none. */
const OI_STUDIES = new Set(['open-interest', 'open-interest-change', 'open-interest-buildup'])

/** The exchanges whose contracts carry open interest. */
const DERIVATIVES = new Set(['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO'])

export function hasOpenInterest(exchange: string): boolean {
  return DERIVATIVES.has(exchange)
}

/**
 * Why a study is not drawn on this instrument, in a sentence, or null when it
 * runs. `facts` is null for an id the registry does not hold (a script that
 * was removed from the server).
 */
export function skipReason(
  id: string,
  facts: StudyFacts | null,
  instrument: { exchange: string; traded: boolean }
): string | null {
  if (id === VOLUME_MA)
    return instrument.traded ? null : 'Volume moving average is not shown: an index has no volume.'
  if (!facts) return 'An indicator in the saved set is not on this server any more.'
  if (OI_STUDIES.has(id) && !hasOpenInterest(instrument.exchange))
    return `${facts.name} is not shown: this instrument has no open interest.`
  if (facts.category === 'Volume' && !instrument.traded)
    return `${facts.name} is not shown: an index has no volume.`
  return null
}

/** One formatter per number of decimals: the legend writes its values on every tick. */
const formats = new Map<number, Intl.NumberFormat>()
function formatOf(decimals: number): Intl.NumberFormat {
  let format = formats.get(decimals)
  if (!format) {
    format = new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    formats.set(decimals, format)
  }
  return format
}

/** A study's values at a bar, for the legend line: a price at the instrument's decimals, else two. */
export function legendValues(
  columns: Readonly<Record<string, readonly (number | null)[]>>,
  plotKeys: readonly string[],
  index: number,
  decimals: number
): string[] {
  return plotKeys.map((key) => {
    const value = columns[key]?.[index]
    return typeof value === 'number' && Number.isFinite(value)
      ? formatOf(decimals).format(value)
      : '—'
  })
}

/** The simple average of the volume of `length` bars ending at `index`; null in the warmup. */
export function volumeMaAt(
  volumes: readonly (number | undefined)[],
  index: number,
  length: number
): number | null {
  if (index + 1 < length || index < 0) return null
  let total = 0
  for (let i = index - length + 1; i <= index; i++) total += volumes[i] ?? 0
  return total / length
}

/** The length a Volume moving average was given, read defensively. */
export function volumeMaLength(settings: Record<string, unknown>): number {
  const n = Math.round(Number(settings.length))
  return Number.isFinite(n) && n >= 2 && n <= 500 ? n : VOLUME_MA_LENGTH
}

/** A study the sheet can add: a built-in, or one of the owner's own scripts (C4). */
export interface CatalogueEntry {
  id: string
  name: string
  category: string
  mine: boolean
}

/** One control of a study's settings on the phone (C4). */
export interface StudyField {
  key: string
  type: 'number' | 'boolean' | 'select' | 'text' | 'interval' | 'time'
  label: string
  tooltip?: string
  options?: ReadonlyArray<{ label: string; value: string }>
  min?: number
  max?: number
  step?: number
  /** Shown only while this holds over the form's values. */
  visibleWhen?: unknown
}

export interface StudyForm {
  name: string
  fields: StudyField[]
  values: Record<string, unknown>
}

const FIELD_TYPES = new Set(['number', 'boolean', 'select', 'text', 'interval', 'time'])

/**
 * A descriptor's inputs as the phone draws them: numbers, switches, choices
 * and text, a price source as a choice among the engine's sources. Colours
 * and line styles stay with the full terminal's form.
 */
export function studyFields(
  inputs: readonly unknown[],
  sources: ReadonlyArray<{ label: string; value: string }>
): StudyField[] {
  const fields: StudyField[] = []
  for (const raw of inputs) {
    const input = raw as Record<string, unknown>
    if (typeof input.key !== 'string' || typeof input.type !== 'string') continue
    const source = input.type === 'source'
    if (!source && !FIELD_TYPES.has(input.type)) continue
    const field: StudyField = {
      key: input.key,
      type: source ? 'select' : (input.type as StudyField['type']),
      label: typeof input.label === 'string' ? input.label : input.key,
    }
    if (typeof input.tooltip === 'string') field.tooltip = input.tooltip
    if (source) field.options = sources
    else if (Array.isArray(input.options))
      field.options = input.options.map((o) =>
        typeof o === 'object' && o !== null
          ? {
              label: String((o as { label: unknown }).label),
              value: String((o as { value: unknown }).value),
            }
          : { label: String(o), value: String(o) }
      )
    for (const bound of ['min', 'max', 'step'] as const)
      if (typeof input[bound] === 'number') field[bound] = input[bound] as number
    if (input.visibleWhen) field.visibleWhen = input.visibleWhen
    fields.push(field)
  }
  return fields
}
