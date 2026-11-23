/**
 * The figures a row shows beside its price (M8.3, GA6.2), free of React like `rows.ts`.
 *
 * Each figure is the tick's field when the row's cache line is newer than the snapshot read (the
 * `indexView` rule) and carries the field, else the snapshot's. A price of 0 or less is not known;
 * volume and open interest may be 0. ATP and IV come only with Quote frames: before the first one
 * they are a dash. A figure that cannot apply (the open interest of a share, the volume of an
 * index) is left blank and said so to a screen reader. Figures carry no colour.
 */
import type { SymbolData } from '@/lib/MarketDataManager'
import { formatAmount, formatCompact, formatPrice } from '@/sudarshan/dashboard/format'
import { isIndex, priceDecimals, type Snapshot } from './rows'
import type { Figure } from './v2/types'

/** What an instrument is, for which figures apply to it. */
export type FigureKind = 'index' | 'share' | 'future' | 'option'

export interface FigureSpec {
  id: Figure
  /** On the row, before the value. */
  label: string
  /** What a screen reader says for it. */
  name: string
  /** It comes only in Quote frames, so a list showing it streams its rows in Quote mode (T1). */
  quote: boolean
  /** The instruments it means something for. */
  kinds: readonly FigureKind[]
}

const ALL: readonly FigureKind[] = ['index', 'share', 'future', 'option']
const TRADED: readonly FigureKind[] = ['share', 'future', 'option']
const spec = (
  id: Figure,
  label: string,
  name: string,
  quote: boolean,
  kinds: readonly FigureKind[]
): FigureSpec => ({ id, label, name, quote, kinds })

/** W1, in the View sheet's order. */
export const FIGURES: readonly FigureSpec[] = [
  spec('high', 'H', 'Day high', true, ALL),
  spec('low', 'L', 'Day low', true, ALL),
  spec('open', 'Open', 'Today’s open', false, ALL),
  spec('prev', 'Prev', 'Previous close', false, ALL),
  spec('volume', 'Vol', 'Volume', true, TRADED),
  spec('oi', 'OI', 'Open interest', true, ['future', 'option']),
  spec('atp', 'ATP', 'Average price', true, TRADED),
  spec('iv', 'IV', 'IV', true, ['option']),
]

const BY_ID = new Map(FIGURES.map((spec) => [spec.id, spec]))

export function specOf(id: Figure): FigureSpec {
  return BY_ID.get(id) as FigureSpec
}

/** The most figures a list shows: two with the phone upright, up to six on its side (W2). */
export const MAX_FIGURES = 6

/** How many columns of two the list's width holds, from its width in rem. */
export function columnsFor(widthPx: number, remPx: number): number {
  if (!(widthPx > 0) || !(remPx > 0)) return 1 // not measured yet: phone first
  const rem = widthPx / remPx
  return rem < 28 ? 1 : rem < 40 ? 2 : 3
}

/** The figures to show at this width, in the order picked; a figure that would not fit is not drawn. */
export function shownFigures(picked: readonly Figure[] | undefined, columns: number): Figure[] {
  return (picked ?? []).filter((id) => BY_ID.has(id)).slice(0, Math.min(MAX_FIGURES, columns * 2))
}

/** Whether what is shown needs Quote frames; a figure hidden at this width costs no data (T1). */
export function needsQuote(shown: readonly Figure[]): boolean {
  return shown.some((id) => specOf(id).quote)
}

const DERIVATIVES = new Set(['NFO', 'BFO', 'MCX', 'CDS', 'BCD', 'NCDEX', 'NCO'])

export function kindOf(symbol: string, exchange: string): FigureKind {
  const venue = exchange.toUpperCase()
  if (isIndex(venue)) return 'index'
  if (!DERIVATIVES.has(venue)) return 'share'
  return /(CE|PE)$/.test(symbol.toUpperCase()) ? 'option' : 'future'
}

const ARTICLE: Record<FigureKind, string> = {
  index: 'an index',
  share: 'a share',
  future: 'a future',
  option: 'an option',
}

const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)

/** A figure's number for a row, or null when it is not known yet. */
export function figureNumber(
  id: Figure,
  snapshot: Snapshot | undefined,
  line: SymbolData | undefined
): number | null {
  const tick = line?.data
  const newer =
    line?.lastUpdate !== undefined && (snapshot === undefined || line.lastUpdate >= snapshot.at)
  const price = (value: unknown) => (finite(value) && value > 0 ? value : null)
  const size = (value: unknown) => (finite(value) && value >= 0 ? value : null)
  const quote = snapshot?.quote
  switch (id) {
    case 'high':
      return (newer ? price(tick?.high) : null) ?? price(quote?.high)
    case 'low':
      return (newer ? price(tick?.low) : null) ?? price(quote?.low)
    case 'open':
      return (newer ? price(tick?.open) : null) ?? price(quote?.open)
    case 'prev':
      // the change line's own close (`indexView`): a tick's whenever the line holds one
      return price(tick?.prev_close) ?? price(quote?.prev_close)
    case 'volume':
      return (newer ? size(tick?.volume) : null) ?? size(quote?.volume)
    case 'oi':
      return (newer ? size(tick?.oi) : null) ?? size(quote?.oi)
    case 'atp':
      return price(tick?.average_price) // no REST source: Quote frames only
    case 'iv':
      return price(tick?.iv) === null ? null : (tick?.iv as number) * 100
  }
}

/** One figure as a row draws it. */
export interface FigureCell {
  id: Figure
  /** A value; `wait` a dash (not received yet); `none` blank (it does not apply to the row). */
  state: 'value' | 'wait' | 'none'
  /** The value as shown, or null. */
  text: string | null
  /** What a screen reader says for the cell. */
  speech: string
}

/** The cells of a row, in the order shown. */
export function cellsOf(
  shown: readonly Figure[],
  symbol: string,
  exchange: string,
  snapshot: Snapshot | undefined,
  line: SymbolData | undefined
): FigureCell[] {
  const kind = kindOf(symbol, exchange)
  return shown.map((id) => {
    const { name, kinds } = specOf(id)
    const noun = id === 'iv' ? 'IV' : name.toLowerCase()
    if (!kinds.includes(kind))
      return { id, state: 'none', text: null, speech: `No ${noun} for ${ARTICLE[kind]}` }
    const value = figureNumber(id, snapshot, line)
    if (value === null) return { id, state: 'wait', text: null, speech: `${name} not received yet` }
    const text =
      id === 'volume' || id === 'oi'
        ? formatCompact(value)
        : id === 'iv'
          ? `${formatAmount(value, 1)}%`
          : formatPrice(value, priceDecimals(exchange))
    return { id, state: 'value', text, speech: `${name} ${text}` }
  })
}

/** Whether a row's cells say the same, so a row whose figures did not move is not redrawn. */
export function sameCells(
  a: readonly FigureCell[] | undefined,
  b: readonly FigureCell[] | undefined
): boolean {
  if (a === b) return true
  if (!a || !b || a.length !== b.length) return false
  return a.every(
    (cell, i) => cell.id === b[i].id && cell.text === b[i].text && cell.speech === b[i].speech
  )
}
