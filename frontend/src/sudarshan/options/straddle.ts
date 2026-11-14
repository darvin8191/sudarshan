/**
 * The straddle of one strike (M11.4, O8), free of React: its figures from the chain model, what it
 * needs followed, and the price each leg hands the computed chart.
 *
 * Feed budget. A straddle on a strike inside the followed window adds nothing to the pooled
 * connection: its two legs are already among the window's instruments, and the chart opens no
 * socket of its own (`chart/computedChart.ts`). A far strike adds its two legs, counted before the
 * window's strikes exactly as an open sheet's instruments are, so the screen never follows more
 * than `FOLLOW_CAP` (`window.ts`).
 */
import type { SymbolData } from '@/lib/MarketDataManager'
import type { ChainModel, StrikeView } from './model'

export interface StraddleFigures {
  strike: number
  /** Call + put, when both have a price. */
  premium: number | null
  /** Against the two previous closes. */
  change: number | null
  changePct: number | null
  /** Strike less premium, and strike plus premium. */
  lower: number | null
  upper: number | null
  /** The premium as a percent of the spot. */
  ofSpotPct: number | null
  atm: boolean
  /** Both legs tick; a far strike's do only once the screen follows them. */
  live: boolean
}

const price = (value: number | null | undefined): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null

/** The strikes a straddle can stand on: those listing both sides, ascending. */
export function straddleStrikes(model: ChainModel): number[] {
  return model.rows.filter((row) => row.ce !== null && row.pe !== null).map((row) => row.strike)
}

/** The strike a straddle opens on: the one asked for when it lists both sides, else the money's, else the nearest to it. */
export function straddleStart(model: ChainModel, asked: number | null): number | null {
  const strikes = straddleStrikes(model)
  if (strikes.length === 0) return null
  if (asked !== null && strikes.includes(asked)) return asked
  if (model.atm !== null && strikes.includes(model.atm)) return model.atm
  const aim = model.atm ?? model.spot ?? strikes[strikes.length >> 1]
  return strikes.reduce((best, each) => (Math.abs(each - aim) < Math.abs(best - aim) ? each : best))
}

/** One step down or up the strikes; the same strike at either end. */
export function stepStrike(model: ChainModel, strike: number, by: -1 | 1): number {
  const strikes = straddleStrikes(model)
  const at = strikes.indexOf(strike)
  if (at < 0) return strike
  return strikes[Math.min(strikes.length - 1, Math.max(0, at + by))]
}

export function straddleFigures(
  row: StrikeView | undefined,
  spot: number | null
): StraddleFigures | null {
  if (!row) return null
  const ce = price(row.ce?.ltp)
  const pe = price(row.pe?.ltp)
  const premium = ce !== null && pe !== null ? ce + pe : null
  const ceClose = price(row.ce?.close)
  const peClose = price(row.pe?.close)
  const before = ceClose !== null && peClose !== null ? ceClose + peClose : null
  const change = premium !== null && before !== null ? premium - before : null
  return {
    strike: row.strike,
    premium,
    change,
    changePct: change !== null && before !== null ? (change / before) * 100 : null,
    lower: premium === null ? null : row.strike - premium,
    upper: premium === null ? null : row.strike + premium,
    ofSpotPct: premium !== null && spot !== null && spot > 0 ? (premium / spot) * 100 : null,
    atm: row.atm,
    live: row.live,
  }
}

/**
 * What the straddle needs followed beside the window (EXCHANGE:SYMBOL): nothing for a strike the
 * window already follows, the two legs for a far one.
 */
export function straddleFollows(
  row: StrikeView | undefined,
  followed: ReadonlySet<number>
): string[] {
  if (!row || !row.ce || !row.pe || followed.has(row.strike)) return []
  return [row.ce.key, row.pe.key]
}

export interface LegTick {
  price: number
  /** The exchange's time of the trade, UTC seconds, when the tick carried it. */
  time: number | null
  /** The cache line it came from: the same line twice is one tick. */
  line: SymbolData
}

/**
 * A leg's tick for the chart, from the screen's own subscription; null for a line that is not a
 * tick off the stream (a quote read standing in for it) or carries no price.
 */
export function legTick(line: SymbolData | undefined): LegTick | null {
  if (!line || line.updateSource !== 'websocket') return null
  const ltp = price(line.data.ltp)
  if (ltp === null) return null
  const traded = line.data.last_trade_time ?? line.data.ltt
  const time =
    typeof traded === 'number' && traded > 0
      ? Math.floor(traded > 1e11 ? traded / 1000 : traded)
      : null
  return { price: ltp, time, line }
}
