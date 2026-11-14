/**
 * A computed chart's arithmetic (M4.13, C11), free of the DOM: the sum of at most two instruments,
 * which on the phone is a straddle (call + put).
 *
 * History. Each leg's candles are read as any instrument's and folded by the chart engine's own
 * expression evaluator (the terminal's `expressionFeed.ts` rules): a bar exists only where every
 * leg has one, its open and close are the sums of the legs', and its high and low are the higher
 * and lower of those two, because the legs' own highs need not have happened together.
 *
 * Live. One price per leg is kept, and each tick of either leg gives one combined tick: the latest
 * price of each, stamped with the later exchange time. The same live-bar builder then forms the
 * candle as it does for one instrument.
 *
 * A computed chart is not an instrument: it has no order book, no volume and cannot be traded.
 */
import type { Bar, BarsRequest, DataFeed } from 'openalgo-charts'

export interface ComputedLeg {
  symbol: string
  exchange: string
}

export interface ComputedSpec {
  legs: readonly ComputedLeg[]
  op: 'sum'
}

/** The most legs a computed chart takes on the phone. */
export const MAX_COMPUTED_LEGS = 2

/** The engine's evaluator reads names; the legs are given letters, so no symbol is ever parsed. */
const NAMES = ['A', 'B'] as const

/** Why a computed chart cannot be drawn from this, in a trader's words; null when it can. */
export function specProblem(spec: ComputedSpec): string | null {
  if (spec.legs.length < 2) return 'A computed chart needs two instruments'
  if (spec.legs.length > MAX_COMPUTED_LEGS)
    return `A computed chart takes at most ${MAX_COMPUTED_LEGS} instruments on the phone`
  if (spec.legs.some((leg) => !leg.symbol || !leg.exchange))
    return 'One side has no contract listed'
  const keys = new Set(spec.legs.map((leg) => `${leg.exchange}:${leg.symbol}`))
  return keys.size === spec.legs.length ? null : 'Both sides name the same instrument'
}

/** What the history owner is asked for: one name for the pair, so a load of another pair is another request. */
export function computedKey(spec: ComputedSpec): string {
  return spec.legs.map((leg) => `${leg.exchange}:${leg.symbol}`).join('+')
}

type TransformTier = typeof import('openalgo-charts/transform')
/** What the fold needs of the engine's transform tier. */
export type Evaluator = Pick<TransformTier, 'evaluateExpression' | 'parseExpression'>

let tier: Promise<TransformTier> | null = null

/**
 * The engine's transform tier, downloaded the first time a computed chart is read and never with
 * the page (`tiers.test.ts`). A download that failed is asked for again by the next read.
 */
export function transformTier(): Promise<TransformTier> {
  tier ??= import('openalgo-charts/transform').catch((error) => {
    tier = null
    throw error
  })
  return tier
}

/** The legs' candles folded into the sum's: only the buckets every leg has. */
export function foldBars(legBars: ReadonlyArray<readonly Bar[]>, engine: Evaluator): Bar[] {
  if (legBars.length < 2 || legBars.length > MAX_COMPUTED_LEGS) return []
  const names = NAMES.slice(0, legBars.length)
  const legs: Record<string, readonly Bar[]> = {}
  names.forEach((name, i) => {
    legs[name] = legBars[i]
  })
  return engine.evaluateExpression(engine.parseExpression(names.join('+')), legs).map((bar) => ({
    time: bar.time,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  }))
}

export interface CombinedTick {
  price: number
  /** The later of the legs' exchange times, UTC seconds; null when no leg carried one. */
  time: number | null
}

/**
 * The latest price of each leg. `set` answers the combined tick, or null while a leg has no price
 * yet. A price of 0 or less is no price and changes nothing.
 */
export class LegPrices {
  private readonly prices: Array<number | null>
  private readonly times: Array<number | null>

  constructor(legs: number) {
    this.prices = Array.from({ length: legs }, () => null)
    this.times = Array.from({ length: legs }, () => null)
  }

  /** Where a leg's history ended: a price for the other leg's first tick to fold with. */
  seed(leg: number, price: number): void {
    if (leg < 0 || leg >= this.prices.length || !(price > 0) || this.prices[leg] !== null) return
    this.prices[leg] = price
  }

  set(leg: number, price: number, time: number | null): CombinedTick | null {
    if (leg < 0 || leg >= this.prices.length || !Number.isFinite(price) || !(price > 0)) return null
    this.prices[leg] = price
    if (time !== null && time > 0) this.times[leg] = time
    return this.combined()
  }

  combined(): CombinedTick | null {
    let price = 0
    for (const each of this.prices) {
      if (each === null) return null
      price += each
    }
    const times = this.times.filter((each): each is number => each !== null)
    return { price, time: times.length ? Math.max(...times) : null }
  }
}

/**
 * History for a computed chart: every leg through the inner feed (the warm cache in front of REST),
 * then folded. The loading controller sees one feed, so the first load, the repair after each bar
 * closes and paging back all work as they do for one instrument. A leg with no bars fails the
 * whole read: a straddle missing a side is not a chart with a gap.
 */
export class ComputedFeed implements DataFeed {
  /** Each leg's last close from the most recent read, for the first combined tick. */
  lastCloses: Array<number | null> = []
  private spec: ComputedSpec | null = null
  private readonly inner: DataFeed

  constructor(inner: DataFeed) {
    this.inner = inner
  }

  setSpec(spec: ComputedSpec): void {
    this.spec = spec
    this.lastCloses = []
  }

  async getBars(req: BarsRequest): Promise<Bar[]> {
    const spec = this.spec
    if (!spec || req.symbol !== computedKey(spec)) return []
    const engine = await transformTier()
    const legs = await Promise.all(
      spec.legs.map(async (leg) => {
        const bars = await this.inner.getBars({
          ...req,
          symbol: leg.symbol,
          exchange: leg.exchange,
        })
        if (!bars.length) throw new Error(`No candles were returned for ${leg.symbol}`)
        return bars
      })
    )
    if (this.spec === spec) this.lastCloses = legs.map((bars) => bars[bars.length - 1].close)
    return foldBars(legs, engine)
  }
}
