/**
 * The option chain as the four tabs read it (M11.1 to M11.5, O3), free of React: one snapshot and one
 * set of ticks in, every figure out. Time and inputs are arguments; nothing here reads a clock.
 *
 * Order of trust. Inside the followed window a tick newer than the snapshot always wins; the snapshot
 * fills what no tick has brought, and alone gives the previous open interest. A tick older than the
 * snapshot loses to it. A price of 0 is no price, and a figure nobody sent is null, never 0.
 *
 * IV and the Greeks are worked out here from these prices (Black-76 on the forward,
 * `lib/optionGreeks.ts`, the option chain's own maths), for the followed strikes only: a far strike's
 * snapshot price is too old to invert. The broker's own IV is kept beside them (`brokerIv`) for the
 * first-session comparison and is not shown.
 */
import type { SymbolData } from '@/lib/MarketDataManager'
import { chainGreeks, type Greeks, priceForGreeks } from '@/lib/optionGreeks'
import { atmStrike } from './window'

/** One leg of the snapshot: the contract and what the broker sent of it (absent, never 0). */
export interface SnapLeg {
  symbol: string
  exchange: string
  lot: number | null
  tick: number | null
  ltp?: number
  bid?: number
  ask?: number
  oi?: number
  /** The previous day's closing open interest (Upstox's page; checked on the first live read). */
  prev_oi?: number
  volume?: number
  close?: number
  iv?: number
}

export interface SnapStrike {
  strike: number
  ce?: SnapLeg
  pe?: SnapLeg
}

export interface ChainSnapshot {
  /** When the server read it, epoch ms. */
  as_of: number
  spot: number | null
  /** ISO day. */
  expiry: string
  /** When the expiry's contracts stop trading, epoch ms. */
  expiry_ts: number
  /** Ascending. */
  strikes: SnapStrike[]
}

export type BuildUp = 'long_buildup' | 'short_buildup' | 'short_covering' | 'long_unwinding'

/** The words of a build-up, as traders say them. */
export const BUILD_UP_WORDS: Record<BuildUp, string> = {
  long_buildup: 'Long build-up',
  short_buildup: 'Short build-up',
  short_covering: 'Short covering',
  long_unwinding: 'Long unwinding',
}

export interface LegView {
  /** EXCHANGE:SYMBOL, the market-data cache key. */
  key: string
  symbol: string
  exchange: string
  lot: number | null
  tick: number | null
  ltp: number | null
  bid: number | null
  ask: number | null
  /** The previous close. */
  close: number | null
  change: number | null
  changePct: number | null
  oi: number | null
  /** Where the open interest came from; null when there is none. */
  oiFrom: 'tick' | 'snapshot' | null
  oiChange: number | null
  /** Of the previous open interest; null when that is 0 or unknown. */
  oiChangePct: number | null
  buildUp: BuildUp | null
  volume: number | null
  greeks: Greeks | null
  /** The broker's own IV as sent, for comparison only. */
  brokerIv: number | null
}

export interface StrikeView {
  strike: number
  ce: LegView | null
  pe: LegView | null
  /** Put open interest over call open interest at this strike. */
  pcr: number | null
  atm: boolean
  /** Both legs are followed on the socket; otherwise the figures are the snapshot's. */
  live: boolean
}

export interface Wall {
  strike: number
  oi: number
}

export interface ChainModel {
  rows: StrikeView[]
  spot: number | null
  atm: number | null
  forward: number | null
  /** What the forward was worked out from. */
  forwardFrom: 'parity' | 'future' | 'spot' | null
  /** Years to the expiry's stop; 0 once it has passed. */
  years: number
  atmIv: number | null
  /** Every put's open interest over every call's, the whole expiry. */
  pcr: number | null
  ceOi: number | null
  peOi: number | null
  maxPain: number | null
  callWall: Wall | null
  putWall: Wall | null
  /** The at-the-money straddle's premium, and it as a percent of the spot. */
  expectedMove: { premium: number; pct: number | null } | null
  /** IV of the put two strikes below the money less IV of the call two above. */
  skew: number | null
  /** When the snapshot was read, epoch ms. */
  asOf: number
  /** The snapshot's time when some strike shows it instead of ticks; null when every strike ticks. */
  wingsAsOf: number | null
}

const YEAR_MS = 365 * 86_400_000

const finite = (value: number | undefined | null): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const positive = (value: number | null): number | null =>
  value !== null && value > 0 ? value : null

/** A tick's figure when it is newer than the snapshot or the snapshot has none; else the snapshot's. */
function pick(
  tick: number | undefined,
  tickAt: number | null,
  snap: number | undefined,
  asOf: number
): { value: number | null; from: 'tick' | 'snapshot' | null } {
  const ticked = finite(tick)
  const read = finite(snap)
  if (ticked !== null && (read === null || (tickAt !== null && tickAt > asOf)))
    return { value: ticked, from: 'tick' }
  return read === null ? { value: null, from: null } : { value: read, from: 'snapshot' }
}

/** The four words: the price against the previous close, the open interest against yesterday's. */
export function buildUpOf(change: number | null, oiChange: number | null): BuildUp | null {
  if (change === null || oiChange === null || change === 0 || oiChange === 0) return null
  if (oiChange > 0) return change > 0 ? 'long_buildup' : 'short_buildup'
  return change > 0 ? 'short_covering' : 'long_unwinding'
}

function legView(leg: SnapLeg, line: SymbolData | undefined, asOf: number): LegView {
  const data = line?.data
  const at = line?.lastUpdate ?? null
  const ltp = positive(pick(data?.ltp, at, leg.ltp, asOf).value)
  const close = positive(pick(data?.close, at, leg.close, asOf).value)
  const oi = pick(data?.oi, at, leg.oi, asOf)
  const previous = finite(leg.prev_oi)
  const change = ltp !== null && close !== null ? ltp - close : null
  const oiChange = oi.value !== null && previous !== null ? oi.value - previous : null
  return {
    key: `${leg.exchange}:${leg.symbol}`,
    symbol: leg.symbol,
    exchange: leg.exchange,
    lot: leg.lot,
    tick: leg.tick,
    ltp,
    bid: positive(pick(data?.bid_price, at, leg.bid, asOf).value),
    ask: positive(pick(data?.ask_price, at, leg.ask, asOf).value),
    close,
    change,
    changePct: change !== null && close !== null ? (change / close) * 100 : null,
    oi: oi.value,
    oiFrom: oi.from,
    oiChange,
    oiChangePct:
      oiChange !== null && previous !== null && previous > 0 ? (oiChange / previous) * 100 : null,
    buildUp: buildUpOf(change, oiChange),
    volume: pick(data?.volume, at, leg.volume, asOf).value,
    greeks: null,
    brokerIv: finite(data?.iv) ?? finite(leg.iv),
  }
}

/** The price a leg's IV is worked out from: its mid when it has a book, else its last; 0 for none. */
export function legPrice(leg: LegView | null): number {
  return leg ? priceForGreeks(leg.ltp ?? undefined, leg.bid ?? undefined, leg.ask ?? undefined) : 0
}

/** One strike's open interest, as the max pain sum reads it (an unknown side counts nothing). */
export interface OiRow {
  strike: number
  ceOi: number | null
  peOi: number | null
}

/**
 * The strike at which the writers pay out least over every strike (the rule of
 * `services/oi_tracker_service.calculate_max_pain`, which the golden vectors come from). A tie goes to
 * the strike nearer the spot, then to the lower. Null with no open interest at all.
 */
export function maxPain(rows: readonly OiRow[], spot: number | null): number | null {
  const chain = rows.filter((row) => row.strike > 0)
  if (!chain.some((row) => (row.ceOi ?? 0) > 0 || (row.peOi ?? 0) > 0)) return null
  let best: { strike: number; pain: number } | null = null
  for (const candidate of chain) {
    let pain = 0
    for (const row of chain) {
      const ce = row.ceOi ?? 0
      const pe = row.peOi ?? 0
      if (candidate.strike > row.strike && ce > 0) pain += (candidate.strike - row.strike) * ce
      if (candidate.strike < row.strike && pe > 0) pain += (row.strike - candidate.strike) * pe
    }
    if (best === null || pain < best.pain) best = { strike: candidate.strike, pain }
    else if (pain === best.pain) {
      const nearer =
        spot === null ? 0 : Math.abs(candidate.strike - spot) - Math.abs(best.strike - spot)
      if (nearer < 0 || (nearer === 0 && candidate.strike < best.strike))
        best = { strike: candidate.strike, pain }
    }
  }
  return best?.strike ?? null
}

/** The highest open interest on one side of the spot; a tie to the strike nearer the spot. */
function wall(rows: readonly OiRow[], spot: number | null, side: 'call' | 'put'): Wall | null {
  if (spot === null) return null
  let best: Wall | null = null
  for (const row of rows) {
    const oi = side === 'call' ? row.ceOi : row.peOi
    if (oi === null || !(oi > 0)) continue
    if (side === 'call' ? row.strike <= spot : row.strike >= spot) continue
    const nearer = best !== null && Math.abs(row.strike - spot) < Math.abs(best.strike - spot)
    if (best === null || oi > best.oi || (oi === best.oi && nearer))
      best = { strike: row.strike, oi }
  }
  return best
}

const sum = (values: Array<number | null>): number | null => {
  const known = values.filter((value): value is number => value !== null)
  return known.length === 0 ? null : known.reduce((total, value) => total + value, 0)
}

const ivOf = (leg: LegView | null | undefined): number | null => positive(leg?.greeks?.iv ?? null)

/** Every figure of one expiry's chain at `now`. */
export function chainModel({
  snapshot,
  lines,
  followed,
  spot: liveSpot = null,
  future = null,
  now,
  ratePct = 0,
}: {
  snapshot: ChainSnapshot
  /** The newest cache line of each followed instrument, by EXCHANGE:SYMBOL. */
  lines: ReadonlyMap<string, SymbolData>
  /** The strikes followed on the socket; null when every strike is. */
  followed: ReadonlySet<number> | null
  /** The underlying's live price; the snapshot's when there is none. */
  spot?: number | null
  /** The nearest future's price, the forward's second source. */
  future?: number | null
  now: number
  ratePct?: number
}): ChainModel {
  const asOf = snapshot.as_of
  const spot = positive(finite(liveSpot)) ?? positive(finite(snapshot.spot))
  const strikes = snapshot.strikes.map((each) => each.strike)
  const atm = atmStrike(spot, strikes)
  const rows: StrikeView[] = snapshot.strikes.map((each) => {
    const live = followed === null || followed.has(each.strike)
    const of = (leg: SnapLeg | undefined) =>
      leg ? legView(leg, live ? lines.get(`${leg.exchange}:${leg.symbol}`) : undefined, asOf) : null
    const ce = of(each.ce)
    const pe = of(each.pe)
    const ceOi = ce?.oi ?? null
    const peOi = pe?.oi ?? null
    return {
      strike: each.strike,
      ce,
      pe,
      pcr: ceOi !== null && peOi !== null && ceOi > 0 ? peOi / ceOi : null,
      atm: each.strike === atm,
      live,
    }
  })

  const middle = rows.find((row) => row.atm) ?? null
  const cePrice = legPrice(middle?.ce ?? null)
  const pePrice = legPrice(middle?.pe ?? null)
  const fromFuture = positive(finite(future))
  const [forward, forwardFrom]: [number | null, ChainModel['forwardFrom']] =
    middle && cePrice > 0 && pePrice > 0
      ? [middle.strike + cePrice - pePrice, 'parity']
      : fromFuture !== null
        ? [fromFuture, 'future']
        : spot !== null
          ? [spot, 'spot']
          : [null, null]
  const years = Math.max(0, snapshot.expiry_ts - now) / YEAR_MS

  const priced = rows.filter((row) => row.live)
  if (forward !== null && years > 0) {
    const greeks = chainGreeks(
      priced.map((row) => ({
        strike: row.strike,
        cePrice: legPrice(row.ce),
        pePrice: legPrice(row.pe),
      })),
      forward,
      years,
      ratePct
    )
    priced.forEach((row, index) => {
      if (row.ce) row.ce.greeks = greeks[index].ce
      if (row.pe) row.pe.greeks = greeks[index].pe
    })
  }

  const oiRows: OiRow[] = rows.map((row) => ({
    strike: row.strike,
    ceOi: row.ce?.oi ?? null,
    peOi: row.pe?.oi ?? null,
  }))
  const ceOi = sum(oiRows.map((row) => row.ceOi))
  const peOi = sum(oiRows.map((row) => row.peOi))
  const at = middle ? rows.indexOf(middle) : -1
  const atmIvs = [ivOf(middle?.ce), ivOf(middle?.pe)].filter((iv): iv is number => iv !== null)
  const putIv = at >= 2 ? ivOf(rows[at - 2].pe) : null
  const callIv = at >= 0 && at + 2 < rows.length ? ivOf(rows[at + 2].ce) : null
  const premium =
    middle?.ce?.ltp != null && middle.pe?.ltp != null ? middle.ce.ltp + middle.pe.ltp : null

  return {
    rows,
    spot,
    atm,
    forward,
    forwardFrom,
    years,
    atmIv: atmIvs.length ? atmIvs.reduce((a, b) => a + b, 0) / atmIvs.length : null,
    pcr: ceOi !== null && peOi !== null && ceOi > 0 ? peOi / ceOi : null,
    ceOi,
    peOi,
    maxPain: maxPain(oiRows, spot),
    callWall: wall(oiRows, spot, 'call'),
    putWall: wall(oiRows, spot, 'put'),
    expectedMove:
      premium === null ? null : { premium, pct: spot !== null ? (premium / spot) * 100 : null },
    skew: putIv !== null && callIv !== null ? putIv - callIv : null,
    asOf,
    wingsAsOf: rows.some((row) => !row.live) ? asOf : null,
  }
}
