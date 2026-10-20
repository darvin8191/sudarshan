/**
 * Whether a chart's prices are live, and the marker it shows when they are not.
 *
 * A chart is live when ticks are arriving over the stream. It is delayed while
 * the stream is down and prices come from a quote request every few seconds,
 * stale when nothing has arrived for a while during the session, and closed
 * outside the session. The words and the order they win in are decided here
 * once, so every chart that shows one says the same thing.
 *
 * Event driven: a tick, a change of transport or a new calendar is what
 * re-evaluates it, plus one timer aimed at the next moment the answer can
 * change by itself (a tick becoming too old, or the session opening or
 * closing). Nothing here polls.
 */
import type { SessionHours } from './liveBars'

export type LiveState = 'live' | 'delayed' | 'stale' | 'closed' | 'connecting'

/** How prices are reaching the chart. */
export type LiveTransport =
  /** The stream is connecting and no quote request has answered yet. */
  | 'connecting'
  /** The stream is up and subscribed. */
  | 'live'
  /** The stream is down; prices come from a quote request every few seconds. */
  | 'polling'

/** What a marker shows, read from a tracker. */
export interface LiveStatus {
  state: LiveState
  /** Wall clock, in milliseconds, when the last price arrived. Null before the first. */
  lastTickAt: number | null
  /** The exchange's time of the last price, in UTC seconds, when the feed sent one. */
  lastTickTime: number | null
  /** How long without a price, during the session, makes the chart stale. */
  staleAfterMs: number
  /** The next session's open, in UTC seconds, while the market is closed and it is known. */
  opensAt: number | null
}

/**
 * How long a chart may go without a price during the session before it is
 * called stale.
 *
 * - **Indices, 10 seconds.** An index is recomputed and sent about once a
 *   second through the session, so ten seconds without one is a stopped feed,
 *   not a quiet market.
 * - **Futures and options, 15 seconds.** Liquid contracts change many times a
 *   second and the book moves between trades, so this is several updates
 *   missed. A far strike can sit still for longer, and on a contract somebody
 *   is trading that is worth seeing rather than hiding.
 * - **Everything else, 30 seconds.** Cash equities, commodities and currencies
 *   include thinly traded names that can go half a minute without a trade.
 */
export function staleAfterMs(exchange: string): number {
  if (exchange.endsWith('_INDEX')) return 10_000
  if (exchange === 'NFO' || exchange === 'BFO' || exchange === 'CRYPTO') return 15_000
  return 30_000
}

export interface LiveStateInput {
  transport: LiveTransport
  /** Whether the exchange is in its regular session now; null when its hours are not known. */
  marketOpen: boolean | null
  /** Milliseconds: when the last price arrived, or null before the first. */
  lastTickAt: number | null
  /** Milliseconds: when the chart started waiting for prices (its subscription). */
  since: number
  now: number
  staleAfterMs: number
}

/**
 * The state, in the order a trader would want to be told: a shut market
 * explains everything else, then a stream that is down, then a stream that is
 * up but silent.
 */
export function liveStateOf(input: LiveStateInput): LiveState {
  if (input.marketOpen === false) return 'closed'
  const age = input.now - (input.lastTickAt ?? input.since)
  if (input.transport === 'polling') return age > input.staleAfterMs ? 'stale' : 'delayed'
  if (input.transport === 'connecting') return age > input.staleAfterMs ? 'stale' : 'connecting'
  if (input.lastTickAt === null) return age > input.staleAfterMs ? 'stale' : 'connecting'
  return age > input.staleAfterMs ? 'stale' : 'live'
}

/** The label a marker carries. Never a protocol word, never a brand. */
export const LIVE_LABEL: Record<LiveState, string> = {
  live: 'Live',
  delayed: 'Delayed',
  stale: 'Stale',
  closed: 'Market closed',
  connecting: 'Connecting',
}

const clock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const clockSeconds = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})
const dayAndClock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  weekday: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** One line under or beside the label: what it means, and what happens next. */
export function liveDetail(status: LiveStatus, now = Date.now()): string {
  switch (status.state) {
    case 'live':
      return 'Prices are updating as they trade.'
    case 'delayed':
      return 'The live stream is reconnecting. Prices refresh every few seconds until it is back.'
    case 'stale':
      // A time rather than an age, so the line stays true however long it is shown.
      return status.lastTickAt === null
        ? 'No price has arrived yet. The prices shown are from history.'
        : `No price update since ${clockSeconds.format(new Date(status.lastTickAt))}. The last price shown may be out of date.`
    case 'closed': {
      if (status.opensAt === null) return 'Prices are from the last session.'
      const opens = new Date(status.opensAt * 1000)
      const sameDay =
        new Date(now).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' }) ===
        opens.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' })
      const at = sameDay
        ? `at ${clock.format(opens)}`
        : dayAndClock.format(opens).replace(', ', ' ')
      return `Opens ${at}. Prices are from the last session.`
    }
    case 'connecting':
      return 'Connecting to live prices.'
  }
}

/** A calendar the tracker can ask about the market's phase. */
export interface StatusCalendar extends SessionHours {
  marketStatusAt?(utcSeconds: number): { changesAt: number | null }
}

export interface LiveStatusTrackerOptions {
  exchange: string
  onChange(status: LiveStatus): void
  /** Milliseconds. */
  now?(): number
  setTimer?(run: () => void, ms: number): unknown
  clearTimer?(handle: unknown): void
}

/** Longest single wait, so a sleeping machine is caught up within this. */
const MAX_WAIT_MS = 30 * 60_000

/** Follows one chart's prices and says when its state changes. */
export class LiveStatusTracker {
  private readonly opts: LiveStatusTrackerOptions
  private exchange: string
  private calendar: StatusCalendar | null = null
  private transport: LiveTransport = 'connecting'
  private lastTickAt: number | null = null
  private lastTickTime: number | null = null
  private since: number
  private timer: unknown = null
  private shown: LiveState | null = null
  private destroyed = false

  constructor(options: LiveStatusTrackerOptions) {
    this.opts = options
    this.exchange = options.exchange
    this.since = this.now()
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now()
  }

  /** A new instrument: nothing has arrived for it yet. */
  reset(exchange: string): void {
    this.exchange = exchange
    this.lastTickAt = null
    this.lastTickTime = null
    this.since = this.now()
    this.update()
  }

  setCalendar(calendar: StatusCalendar | null): void {
    this.calendar = calendar
    this.update()
  }

  setTransport(transport: LiveTransport): void {
    if (transport === this.transport) return
    // While quote requests are answering, another failed attempt to reconnect
    // the stream changes nothing a trader can see: still delayed, not
    // connecting. Only the stream coming back ends the fallback.
    if (transport === 'connecting' && this.transport === 'polling') return
    // The silence of a stream that has just come up is measured from now.
    if (transport === 'live' || transport === 'polling') this.since = this.now()
    this.transport = transport
    this.update()
  }

  /** A price arrived. `time` is the exchange's time for it, in UTC seconds, when known. */
  noteTick(time?: number): void {
    this.lastTickAt = this.now()
    if (typeof time === 'number' && Number.isFinite(time)) this.lastTickTime = time
    // Ticks arrive several times a second; only a change of state is worth a
    // callback, and the timer already in place is re-aimed when it fires.
    if (this.shown !== 'live' || this.timer === null) this.update()
  }

  status(): LiveStatus {
    const now = this.now()
    const marketOpen = this.calendar ? this.calendar.sessionAt(now / 1000) !== null : null
    const state = liveStateOf({
      transport: this.transport,
      marketOpen,
      lastTickAt: this.lastTickAt,
      since: this.since,
      now,
      staleAfterMs: staleAfterMs(this.exchange),
    })
    let opensAt: number | null = null
    if (state === 'closed' && this.calendar) {
      const next = this.calendar.sessionFrom?.(now / 1000)
      opensAt = next ? next.open : null
    }
    return {
      state,
      lastTickAt: this.lastTickAt,
      lastTickTime: this.lastTickTime,
      staleAfterMs: staleAfterMs(this.exchange),
      opensAt,
    }
  }

  destroy(): void {
    this.destroyed = true
    this.clearTimer()
  }

  private clearTimer(): void {
    if (this.timer === null) return
    if (this.opts.clearTimer) this.opts.clearTimer(this.timer)
    else clearTimeout(this.timer as ReturnType<typeof setTimeout>)
    this.timer = null
  }

  private update(): void {
    if (this.destroyed) return
    const status = this.status()
    if (status.state !== this.shown) {
      this.shown = status.state
      this.opts.onChange(status)
    }
    this.aim(status)
  }

  /** One timer, at the next instant the state can change without an event. */
  private aim(status: LiveStatus): void {
    this.clearTimer()
    const now = this.now()
    const waits: number[] = []
    if (status.state !== 'closed') {
      const from = this.lastTickAt ?? this.since
      waits.push(from + status.staleAfterMs - now + 1)
    }
    const changesAt = this.calendar?.marketStatusAt?.(now / 1000)?.changesAt
    if (typeof changesAt === 'number' && Number.isFinite(changesAt))
      waits.push(changesAt * 1000 - now + 1)
    const pending = waits.filter((ms) => ms > 0)
    if (!pending.length) return
    const wait = Math.min(MAX_WAIT_MS, ...pending)
    const run = () => {
      this.timer = null
      this.update()
    }
    this.timer = this.opts.setTimer ? this.opts.setTimer(run, wait) : setTimeout(run, wait)
  }
}
