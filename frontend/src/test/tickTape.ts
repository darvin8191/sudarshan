/**
 * Tick tapes (M4.1, C13): what OpenAlgo's market-data proxy sends a browser,
 * frame by frame and moment by moment, played against a fake clock.
 *
 * A tape is a small JSON file in `src/test/tapes/`, synthesised in the proxy's
 * own message shape (LTP, Quote with `oi`, `iv` and `greeks`, Depth) by the
 * builders below, from a scenario stated in words in `tapes/scenarios.ts`.
 * None is a recording: recorded market data never enters this public
 * repository. `tickTape.test.ts` holds every committed tape to exactly what
 * its scenario builds; after changing a scenario, run
 * `npx vitest run src/test/tickTape.test.ts -u` and commit what it rewrites.
 *
 * One tape, three ways in:
 * - a screen on the shared feed (`MarketDataManager`, replaced by
 *   `marketDataHarness.ts`): `harnessSink(feed)`;
 * - the phone chart's own socket (`OpenAlgoWsFeed`): `FakeProxySocket`
 *   stubbed in as `WebSocket`, and `socketSink`;
 * - a headless browser: the same JSON over `page.routeWebSocket`.
 *
 * Besides frames, a tape says when the page was hidden or shown, and when the
 * phone slept: Android stops the clock Chrome's timers run on while the
 * device sleeps, so a timer set before the sleep fires its remaining wait
 * after the wake while the wall clock has jumped. A test plays that with
 * `vi.setSystemTime`, which moves the wall clock and keeps every timer's
 * remaining wait, exactly that.
 */
import type { MarketData } from '@/lib/MarketDataManager'

/** The proxy's mode numbers: 1 LTP, 2 Quote, 3 Depth. */
export type ProxyMode = 1 | 2 | 3

/** One market-data message as the proxy forwards it to a browser. */
export interface ProxyFrame {
  type: 'market_data'
  symbol: string
  exchange: string
  mode: ProxyMode
  data: Record<string, unknown>
}

export type TapeEntry =
  /** The proxy sends this frame. */
  | { at: number; frame: ProxyFrame }
  /** The page is hidden (screen off, another app) or shown again. */
  | { at: number; page: 'hidden' | 'visible' }
  /** The phone sleeps this many milliseconds: the wall clock jumps, timers wait. */
  | { at: number; sleep: number }

export interface Tape {
  name: string
  /** The scenario, in words. */
  scenario: string
  /** Epoch milliseconds of the tape's first moment; every `at` counts from it. */
  start: number
  entries: TapeEntry[]
}

/** A tape as its committed file holds it: one entry to a line, so a diff reads frame by frame. */
export function formatTape(tape: Tape): string {
  const { entries, ...head } = tape
  const fields = Object.entries(head).map(
    ([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)},`
  )
  const lines = entries.map((entry) => `    ${JSON.stringify(entry)}`)
  return `{\n${fields.join('\n')}\n  "entries": [\n${lines.join(',\n')}\n  ]\n}\n`
}

/** A trade or book update, as the builders take it. Times are epoch milliseconds. */
export interface TickSpec {
  ltp: number
  /** When the proxy stamped the frame. */
  at: number
  /** When the last trade happened; absent on a frame that says nothing of it. */
  tradeAt?: number
  /** The day's traded quantity so far. */
  volume?: number
  /** The last trade's quantity. */
  ltq?: number
  /** The previous session's close. */
  prevClose?: number
}

export interface QuoteSpec extends TickSpec {
  open?: number
  high?: number
  low?: number
  oi?: number
  /** As Upstox sends it: a fraction, 0.1314 for 13.14%. */
  iv?: number
  greeks?: { delta: number; gamma: number; theta: number; vega: number; rho: number }
}

export interface DepthSpec extends QuoteSpec {
  /** Best bid and offer with their quantities; the other four levels are the proxy's zeros. */
  bid?: { price: number; quantity: number }
  ask?: { price: number; quantity: number }
}

const round2 = (n: number) => Math.round(n * 100) / 100

/** An LTP-mode frame: the price, the trade's time and quantity, the previous close. */
export function ltpFrame(symbol: string, exchange: string, tick: TickSpec): ProxyFrame {
  const data: Record<string, unknown> = {
    ltp: tick.ltp,
    ltq: tick.ltq ?? 0,
    ltt: tick.tradeAt ?? 0,
    cp: tick.prevClose ?? 0,
    timestamp: tick.at,
  }
  if (tick.prevClose !== undefined) data.prev_close = tick.prevClose
  if (tick.tradeAt !== undefined) data.last_trade_time = tick.tradeAt
  return { type: 'market_data', symbol, exchange, mode: 1, data }
}

function quoteData(tick: QuoteSpec): Record<string, unknown> {
  const data: Record<string, unknown> = {
    open: tick.open ?? 0,
    high: tick.high ?? 0,
    low: tick.low ?? 0,
    // The previous session's close, or 0.0 when not known, as the proxy sends it.
    close: tick.prevClose ?? 0,
    ltp: tick.ltp,
    last_trade_quantity: tick.ltq ?? 0,
    volume: tick.volume ?? 0,
    timestamp: tick.at,
  }
  if (tick.prevClose !== undefined) data.prev_close = tick.prevClose
  if (tick.tradeAt !== undefined) data.last_trade_time = tick.tradeAt
  if (tick.oi !== undefined) data.oi = tick.oi
  if (tick.iv !== undefined) data.iv = tick.iv
  if (tick.greeks) data.greeks = { ...tick.greeks }
  return data
}

/** A Quote-mode frame: the day's figures, and `oi`, `iv` and `greeks` where given. */
export function quoteFrame(symbol: string, exchange: string, tick: QuoteSpec): ProxyFrame {
  return { type: 'market_data', symbol, exchange, mode: 2, data: quoteData(tick) }
}

/** A Depth-mode frame: the Quote fields beside five levels a side. */
export function depthFrame(symbol: string, exchange: string, tick: DepthSpec): ProxyFrame {
  const zero = { price: 0, quantity: 0, orders: 0 }
  const side = (best: { price: number; quantity: number } | undefined) => [
    best ? { price: best.price, quantity: best.quantity, orders: 0 } : zero,
    zero,
    zero,
    zero,
    zero,
  ]
  const bid = tick.bid ?? { price: round2(tick.ltp - 0.05), quantity: 650 }
  const ask = tick.ask ?? { price: tick.ltp, quantity: 650 }
  return {
    type: 'market_data',
    symbol,
    exchange,
    mode: 3,
    data: { ...quoteData(tick), depth: { buy: side(bid), sell: side(ask) } },
  }
}

/** Where a played tape goes. */
export interface TapeSinks {
  frame(frame: ProxyFrame): void
  page?(state: 'hidden' | 'visible'): void
  /**
   * The phone slept: move the wall clock by `ms` and leave every timer's
   * remaining wait. Without it, a sleep is time passing with timers running,
   * which is a page that was only hidden.
   */
  sleep?(ms: number): void | Promise<void>
}

/**
 * Plays a tape step by step. `to(at)` delivers every entry up to and including
 * `at` (milliseconds from the tape's start) in order, moving the fake clock
 * between them with `advance`, and leaves the clock at `at`, so a test can
 * stop at any moment, look, and go on.
 */
export class TapePlayer {
  private index = 0
  private reached = 0
  private readonly tape: Tape
  private readonly sinks: TapeSinks
  private readonly advance: (ms: number) => Promise<unknown>

  constructor(tape: Tape, sinks: TapeSinks, advance: (ms: number) => Promise<unknown>) {
    this.tape = tape
    this.sinks = sinks
    this.advance = advance
  }

  /** The tape time reached, milliseconds from its start. */
  get at(): number {
    return this.reached
  }

  /** The epoch milliseconds of a moment on the tape. */
  time(at: number): number {
    return this.tape.start + at
  }

  async to(at: number): Promise<void> {
    const entries = this.tape.entries
    while (this.index < entries.length && entries[this.index].at <= at) {
      const entry = entries[this.index++]
      if (entry.at > this.reached) {
        await this.advance(entry.at - this.reached)
        this.reached = entry.at
      }
      if ('frame' in entry) this.sinks.frame(entry.frame)
      else if ('page' in entry) this.sinks.page?.(entry.page)
      else {
        // Without a sleep of its own, a sink sees the time pass with its timers running.
        if (this.sinks.sleep) await this.sinks.sleep(entry.sleep)
        else await this.advance(entry.sleep)
        this.reached += entry.sleep
      }
    }
    if (at > this.reached) {
      await this.advance(at - this.reached)
      this.reached = at
    }
  }

  /** Play what is left. */
  async end(): Promise<void> {
    const last = this.tape.entries.at(-1)
    await this.to(last ? last.at + ('sleep' in last ? last.sleep : 0) : this.reached)
  }
}

/** The tape's frames into the shared feed's stand-in, stamped with the fake clock. */
export function harnessSink(feed: {
  push(symbol: string, exchange: string, data: MarketData, source?: 'websocket' | 'rest'): void
}): (frame: ProxyFrame) => void {
  return (frame) => feed.push(frame.symbol, frame.exchange, frame.data as MarketData, 'websocket')
}

/**
 * The proxy's side of one browser WebSocket, stubbed in as `WebSocket`:
 * `vi.stubGlobal('WebSocket', FakeProxySocket)`. Every socket the page opens
 * is kept in `FakeProxySocket.all`, so a test can tell a replacement from the
 * original; the proxy only speaks when the test says so (`accept`, `receive`,
 * `pong`), which is what makes a silent socket possible.
 */
export class FakeProxySocket {
  static all: FakeProxySocket[] = []
  static latest(): FakeProxySocket | undefined {
    return FakeProxySocket.all.at(-1)
  }

  readonly url: string
  readyState = 0
  sent: Array<Record<string, unknown>> = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeProxySocket.all.push(this)
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data))
  }

  close(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.()
  }

  /** The proxy accepts the connection and the key. */
  accept(): void {
    this.readyState = 1
    this.onopen?.()
    this.receive({ type: 'auth', status: 'success' })
  }

  /** The connection drops from the far end. */
  drop(): void {
    this.readyState = 3
    this.onclose?.()
  }

  receive(message: object): void {
    if (this.readyState === 3) return
    this.onmessage?.({ data: JSON.stringify(message) })
  }

  subscriptions(): Array<Record<string, unknown>> {
    return this.sent.filter((message) => message.action === 'subscribe')
  }

  pings(): Array<Record<string, unknown>> {
    return this.sent.filter((message) => message.action === 'ping')
  }

  /** The proxy answers the last ping, its clock `aheadMs` ahead of the phone's. */
  pong(aheadMs = 0): void {
    const ping = this.pings().at(-1)
    if (!ping) throw new Error('no ping to answer')
    this.receive({
      type: 'pong',
      status: 'success',
      server_timestamp: Date.now() + aheadMs,
      client_timestamp: ping.timestamp,
      _pingId: ping._pingId,
    })
  }
}

/** A tape's frames into whichever socket is open now; a frame sent while none is, is lost. */
export function socketSink(): (frame: ProxyFrame) => void {
  return (frame) => {
    const socket = FakeProxySocket.latest()
    if (socket?.readyState === 1) socket.receive(frame)
  }
}
