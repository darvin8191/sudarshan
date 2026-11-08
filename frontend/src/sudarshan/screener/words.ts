/**
 * A clause as the owner reads it: a sentence of chips, `[15 min] [EMA 20] [crosses above]
 * [15 min] [EMA 50]` (design 5.2). Plain functions, so a test can call them.
 */
import {
  FIELD_BY_ID,
  GROUP_TITLES,
  INDICATOR_BY_ID,
  type IndicatorInfo,
  OPERATOR_BY_ID,
  OPTION_BY_ID,
} from './catalog'
import type { Clause, Offset, Operand, TimeFrame } from './types'

export function tfWords(tf: TimeFrame | undefined): string {
  if (tf === undefined) return ''
  if (tf === 'D') return 'Day'
  if (tf === 'W') return 'Week'
  return tf === 60 ? '60 min' : `${tf} min`
}

export function offsetWords(at: Offset | undefined): string {
  if (at === undefined || at === 0) return 'Latest candle'
  if (at === 'first_today') return "Today's first candle"
  if (at === 'last_prev_day') return "Yesterday's last candle"
  return at.ago === 1 ? '1 candle ago' : `${at.ago} candles ago`
}

const plainNumber = (value: unknown): string => String(value)

/** 'EMA 20', 'MACD 12, 26, 9', 'MACD signal': the label, its parameters and a non-default output. */
export function indicatorWords(
  ind: string,
  params: Record<string, number | string> | undefined,
  out?: string
): string {
  const info: IndicatorInfo | undefined = INDICATOR_BY_ID.get(ind)
  if (!info) return ind
  const shown = info.params
    .map((param) => ({ param, value: params?.[param.name] ?? param.default }))
    // A source or kind left at its default is not worth a word; one the owner changed is.
    .filter(({ param, value }) => param.kind !== 'choice' || value !== param.default)
  const numbers = shown
    .filter(({ param }) => param.name !== 'source')
    .map(({ value }) => plainNumber(value))
  const source = shown.find(({ param }) => param.name === 'source')
  const base = numbers.length > 0 ? `${info.label} ${numbers.join(', ')}` : info.label
  const title = source ? `${base} of ${plainNumber(source.value).replace(/_/g, ' ')}` : base
  return out && out !== info.outs[0] ? `${title} ${out.replace(/_/g, ' ')}` : title
}

export interface Chips {
  /** The timeframe chip, when the operand reads candles. */
  tf: string | null
  /** The operand itself. */
  text: string
  /** The offset chip, when it is not the latest candle. */
  at: string | null
}

const BUILDUP = {
  long_buildup: 'Long build-up',
  short_buildup: 'Short build-up',
  short_covering: 'Short covering',
  long_unwinding: 'Long unwinding',
  none: 'None',
} as const

export function operandChips(operand: Operand | undefined): Chips {
  if (!operand) return { tf: null, text: '', at: null }
  if ('num' in operand) return { tf: null, text: operand.num, at: null }
  if ('field' in operand) {
    const info = FIELD_BY_ID.get(operand.field)
    return {
      tf: info?.candle ? tfWords(operand.tf) : null,
      text: info?.label ?? operand.field,
      at:
        info?.candle && operand.at !== undefined && operand.at !== 0
          ? offsetWords(operand.at)
          : null,
    }
  }
  if ('ind' in operand) {
    return {
      tf: tfWords(operand.tf),
      text: indicatorWords(operand.ind, operand.p, operand.out),
      at: operand.at !== undefined && operand.at !== 0 ? offsetWords(operand.at) : null,
    }
  }
  if ('oi' in operand) {
    const what = operand.oi === 'buildup' ? 'OI build-up' : 'OI change %'
    const window = operand.window.kind === 'day' ? 'today' : `${operand.window.n} candles`
    return {
      tf: operand.window.kind === 'day' ? null : tfWords(operand.tf),
      text: `${what} (${window})`,
      at: null,
    }
  }
  if ('opt' in operand) {
    const info = OPTION_BY_ID.get(operand.opt)
    const base = info?.label ?? operand.opt
    const of = operand.underlying && operand.underlying !== 'self' ? ` (${operand.underlying})` : ''
    return { tf: info?.timed ? tfWords(operand.tf) : null, text: `${base}${of}`, at: null }
  }
  const sign = { add: '+', sub: '-', mul: '×', div: '÷' }[operand.math]
  const [a, b] = operand.args.map((arg) => inline(arg))
  return { tf: null, text: `${a} ${sign} ${b}`, at: null }
}

/** An operand on one line, for a calculation: '1.5 × SMA 20 (Volume)'. */
function inline(operand: Operand): string {
  const { tf, text, at } = operandChips(operand)
  return [tf, text, at].filter(Boolean).join(' ')
}

export const operatorWords = (id: string): string => OPERATOR_BY_ID.get(id)?.label ?? id

/**
 * The operator chip: its words with the arguments written in, as the owner reads them: 'Crosses
 * above', 'Is within 0.5 % of', 'Is rising for 3 candles', 'Is between 30 and 70', 'Is Doji'.
 */
export function operatorChip(clause: Pick<Clause, 'op' | 'args'>): string {
  const args = clause.args ?? {}
  const word = (value: unknown) => (value === undefined || value === '' ? '?' : String(value))
  switch (clause.op) {
    case 'between':
      return `Is between ${word(args.low)} and ${word(args.high)}`
    case 'within_pct':
      return `Is within ${word(args.pct)} % of`
    case 'rising':
      return `Is rising for ${word(args.n)} candles`
    case 'falling':
      return `Is falling for ${word(args.n)} candles`
    case 'is':
      return `Is ${typeof args.name === 'string' ? stateWords(args.name) : '?'}`
    default:
      return operatorWords(clause.op)
  }
}

/** 'bullish_engulfing' -> 'Bullish engulfing'; a build-up state in its own words. */
export function stateWords(name: string): string {
  if (name in BUILDUP) return BUILDUP[name as keyof typeof BUILDUP]
  const spaced = name.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** The whole clause as one sentence, for a screen reader. */
export function clauseSentence(clause: Clause): string {
  const info = OPERATOR_BY_ID.get(clause.op)
  const parts = [inline(clause.left), operatorChip(clause).toLowerCase()]
  if (info?.arity === 'binary' && clause.right) parts.push(inline(clause.right))
  return parts.join(' ')
}

export { GROUP_TITLES }
