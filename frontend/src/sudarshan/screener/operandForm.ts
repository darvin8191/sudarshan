/**
 * What the operand sheet offers and builds (design 5.2), as plain functions a test calls: the
 * five tabs (Price, Indicators, Volume, OI & options, Number), the items of each, an operand
 * started from an item with the screen's own timeframe, and the range of each parameter in words.
 */
import {
  BUILDUP_STATES,
  FIELD_BY_ID,
  FIELDS,
  INDICATOR_BY_ID,
  INDICATORS,
  type IndicatorInfo,
  OPTION_BY_ID,
  OPTIONS,
  type ParamInfo,
  TIMEFRAMES,
} from './catalog'
import type { Offset, Operand, TimeFrame } from './types'

export type OperandTab = 'price' | 'indicators' | 'volume' | 'oi' | 'number'

export const OPERAND_TABS: Array<{ id: OperandTab; label: string }> = [
  { id: 'price', label: 'Price' },
  { id: 'indicators', label: 'Indicators' },
  { id: 'volume', label: 'Volume' },
  { id: 'oi', label: 'OI & options' },
  { id: 'number', label: 'Number' },
]

export type Pick =
  | { kind: 'field'; id: string }
  | { kind: 'ind'; id: string }
  | { kind: 'oi'; what: 'buildup' | 'oi_change_pct' }
  | { kind: 'opt'; id: string }
  | { kind: 'num' }
  | { kind: 'math' }

export interface Item {
  key: string
  label: string
  tab: OperandTab
  pick: Pick
}

const VOLUME_FIELDS = new Set(['volume', 'turnover'])
const VOLUME_INDICATORS = new Set(['obv', 'rvol'])

const fieldItem = (id: string, tab: OperandTab): Item => ({
  key: `field:${id}`,
  label: FIELD_BY_ID.get(id)?.label ?? id,
  tab,
  pick: { kind: 'field', id },
})

export const ITEMS: Item[] = [
  ...FIELDS.filter((f) => f.id !== 'oi' && !VOLUME_FIELDS.has(f.id)).map((f) =>
    fieldItem(f.id, 'price')
  ),
  ...INDICATORS.filter((i) => !VOLUME_INDICATORS.has(i.id)).map(
    (i): Item => ({
      key: `ind:${i.id}`,
      label: i.label,
      tab: 'indicators',
      pick: { kind: 'ind', id: i.id },
    })
  ),
  ...[...VOLUME_FIELDS].map((id) => fieldItem(id, 'volume')),
  ...INDICATORS.filter((i) => i.id === 'obv' || i.id === 'rvol').map(
    (i): Item => ({
      key: `ind:${i.id}`,
      label: i.label,
      tab: 'volume',
      pick: { kind: 'ind', id: i.id },
    })
  ),
  fieldItem('oi', 'oi'),
  { key: 'oi:buildup', label: 'OI build-up', tab: 'oi', pick: { kind: 'oi', what: 'buildup' } },
  {
    key: 'oi:oi_change_pct',
    label: 'OI change %',
    tab: 'oi',
    pick: { kind: 'oi', what: 'oi_change_pct' },
  },
  ...OPTIONS.map(
    (o): Item => ({
      key: `opt:${o.id}`,
      label: o.label,
      tab: 'oi',
      pick: { kind: 'opt', id: o.id },
    })
  ),
  { key: 'num', label: 'A number', tab: 'number', pick: { kind: 'num' } },
]

export const itemsOf = (tab: OperandTab): Item[] => ITEMS.filter((item) => item.tab === tab)

/** Items whose words contain what was typed, across every tab; none for an empty search. */
export function searchItems(text: string): Item[] {
  const needle = text.trim().toLowerCase()
  if (!needle) return []
  return ITEMS.filter((item) => item.label.toLowerCase().includes(needle))
}

/** The tab an item is on: where the sheet opens for an operand already chosen. */
export function tabOfPick(pick: Pick): OperandTab {
  const key =
    pick.kind === 'field' || pick.kind === 'ind' || pick.kind === 'opt'
      ? `${pick.kind}:${pick.id}`
      : pick.kind === 'oi'
        ? `oi:${pick.what}`
        : pick.kind
  return ITEMS.find((item) => item.key === key)?.tab ?? 'price'
}

/** Which item an operand is: what the sheet shows selected when it opens on it. */
export function pickOf(operand: Operand): Pick {
  if ('num' in operand) return { kind: 'num' }
  if ('field' in operand) return { kind: 'field', id: operand.field }
  if ('ind' in operand) return { kind: 'ind', id: operand.ind }
  if ('oi' in operand) return { kind: 'oi', what: operand.oi }
  if ('opt' in operand) return { kind: 'opt', id: operand.opt }
  return { kind: 'math' }
}

/** The timeframes a pick can be read on: every one, or what the indicator allows. */
export function timeframesOf(pick: Pick): TimeFrame[] {
  if (pick.kind === 'ind') return INDICATOR_BY_ID.get(pick.id)?.tfs ?? TIMEFRAMES
  return TIMEFRAMES
}

/** Whether a pick reads candles (so takes a timeframe and an offset). */
export function readsCandles(pick: Pick): boolean {
  if (pick.kind === 'field') return FIELD_BY_ID.get(pick.id)?.candle === true
  return pick.kind === 'ind'
}

/** The indicator's parameters at their defaults. */
export function defaultsOf(info: IndicatorInfo): Record<string, number | string> {
  return Object.fromEntries(info.params.map((param) => [param.name, param.default]))
}

/** An operand started from an item, on the timeframe the screen's own candles are. */
export function starting(pick: Pick, tf: TimeFrame): Operand {
  switch (pick.kind) {
    case 'num':
      return { num: '0' }
    case 'field':
      return FIELD_BY_ID.get(pick.id)?.candle ? { field: pick.id, tf } : { field: pick.id }
    case 'ind': {
      const info = INDICATOR_BY_ID.get(pick.id)
      const allowed = info?.tfs
      const chosen = allowed && !allowed.includes(tf) ? allowed[0] : tf
      return { ind: pick.id, p: info ? defaultsOf(info) : {}, tf: chosen }
    }
    case 'oi':
      return { oi: pick.what, window: { kind: 'day' } }
    case 'opt': {
      const info = OPTION_BY_ID.get(pick.id)
      return {
        opt: pick.id,
        ...(info?.underlying ? { underlying: 'NIFTY' } : {}),
        ...(info?.timed ? { tf } : {}),
      }
    }
    default:
      return { num: '0' }
  }
}

/** The range of a parameter in words: 'a whole number from 2 to 200'. */
export function rangeWords(param: ParamInfo): string {
  if (param.kind === 'choice') return ''
  const low = param.low ?? null
  const high = param.high ?? null
  const what = param.kind === 'int' ? 'a whole number' : 'a number'
  if (low !== null && high !== null) return `${what} from ${low} to ${high}`
  return what
}

/** 'length' -> 'length', 'atr_length' -> 'ATR length': a parameter as a sentence names it. */
const lowerName = (name: string) => name.replace(/_/g, ' ')

/**
 * The service's sentence when a parameter value is outside what the indicator takes, else null:
 * "RSI length must be a whole number from 2 to 200." (spec.check words, vectors SE40 and SE52).
 */
export function paramError(info: IndicatorInfo, param: ParamInfo, value: unknown): string | null {
  const who = `${info.label} ${lowerName(param.name)}`
  if (param.kind === 'choice') {
    return param.choices.includes(String(value))
      ? null
      : `${who} must be one of: ${param.choices.join(', ')}.`
  }
  const text = String(value ?? '').trim()
  const n = Number(text)
  const valid = text !== '' && Number.isFinite(n) && (param.kind === 'dec' || Number.isInteger(n))
  const inside =
    valid && (param.low === null || n >= param.low) && (param.high === null || n <= param.high)
  return inside ? null : `${who} must be ${rangeWords(param)}.`
}

/** Each parameter of an indicator operand that is out of range, by name. */
export function operandProblems(operand: Operand): Map<string, string> {
  const out = new Map<string, string>()
  if (
    'at' in operand &&
    typeof operand.at === 'object' &&
    (operand.at.ago < 1 || operand.at.ago > 100)
  ) {
    out.set('at', operand.at.ago > 100 ? AGO_WORDS : 'Pick 1 candle ago or more.')
  }
  if (!('ind' in operand)) return out
  const info = INDICATOR_BY_ID.get(operand.ind)
  if (!info) return out
  for (const param of info.params) {
    const error = paramError(info, param, operand.p?.[param.name] ?? param.default)
    if (error) out.set(param.name, error)
  }
  return out
}

/** Typed numbers: the service's sentence (vector SE37). */
export const NUMBER_WORDS = 'Type a number, like 55 or 2500.50.'

/** What a minute-candle offset says on a day or week candle (vectors SE4 and SE4b), or null. */
export function offsetRefusal(at: Offset | undefined, tf: TimeFrame | undefined): string | null {
  if (typeof tf === 'number' || tf === undefined) return null
  if (at !== 'first_today' && at !== 'last_prev_day') return null
  const what = at === 'first_today' ? "Today's first candle" : "Yesterday's last candle"
  return `${what} is for minute candles. For ${tf === 'D' ? 'day' : 'week'} candles, use 1 candle ago.`
}

/** The service's sentence for looking back too far (vector SE35). */
export const AGO_WORDS = 'You can look back 100 candles at most.'

export const BUILDUP_NAMES = BUILDUP_STATES
