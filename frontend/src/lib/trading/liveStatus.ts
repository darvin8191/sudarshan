/**
 * Whether a chart's prices are arriving in real time, and what its marker
 * says when they are not.
 *
 * The rules and the words are the app's own (`sudarshan/dashboard/live.ts`,
 * `feedLine`): "Real-time" only while the stream is delivering ticks;
 * "Delayed" with the age of the figures while the stream is down and a quote
 * request every few seconds fills in; "Reconnecting" while it is down with
 * nothing filling in; "Stale" when nothing has come for a while in market
 * hours; "Market closed" outside them. This file only follows one chart's
 * inputs over time and asks those rules again when the answer can change.
 *
 * The calendar says when the market is open, and trades say so too: stream
 * ticks whose trade times keep advancing are a market trading, so the chart
 * says "Real-time" while they flow even when the calendar says closed (a
 * special session the holiday list does not carry), and never "Market
 * closed" over prices that are moving.
 *
 * Event driven: a tick, a change of transport or a new calendar re-evaluates
 * it, plus one timer aimed at the next moment the answer can change by itself
 * (prices growing old, or the session opening or closing). Nothing here polls.
 */
import { type FeedLine, feedLine, sameFeedState } from '@/sudarshan/dashboard/live'
import { FLOW_GAP_SEC, type SessionHours } from './liveBars'

/** How prices are reaching the chart. */
export type LiveTransport =
  /** The stream is not up yet, or is down with nothing filling in. */
  | 'connecting'
  /** The stream is up and subscribed. */
  | 'live'
  /** The stream is down; prices come from a quote request every few seconds. */
  | 'polling'

/** What a marker shows, read from a tracker. */
export interface LiveStatus {
  /** What the feed is doing, in the words the whole app uses. */
  line: FeedLine
  /** Wall clock, in milliseconds, of the last price the stream delivered. */
  lastTickAt: number | null
  /** Wall clock, in milliseconds, of the last price from anywhere: the stream, a quote or history. */
  lastUpdateAt: number | null
  /** The exchange's time of the last price, in UTC seconds, when the feed sent one. */
  lastTickTime: number | null
  /** How long without a tick, in market hours, makes the chart's prices stale. */
  staleAfterMs: number
  /** The next session's open, in UTC seconds, while the market is closed and it is known. */
  opensAt: number | null
}

/**
 * How long a chart may go without a tick in market hours before its prices
 * are called stale.
 *
 * - **Indices, 10 seconds.** An index is recomputed and sent about once a
 *   second through the session, so ten seconds without one is a stopped feed,
 *   not a quiet market. The dashboard uses the same figure for its indices.
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

/** Whether a line is worth a marker on a chart that is otherwise quiet about its feed. */
export function needsMarker(line: FeedLine): boolean {
  switch (line.kind) {
    case 'live':
    case 'waiting':
      return false
    case 'delayed':
      // Fresh from history and waiting for the first tick is the normal half
      // second after every load; only an old figure is worth saying.
      return line.stale
    default:
      return true
  }
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

/**
 * One sentence on what the marker means and what happens next, for its
 * tooltip and for a screen reader. Times rather than ages, so it stays true
 * however long it is shown.
 */
export function liveDetail(status: LiveStatus, now = Date.now()): string {
  const line = status.line
  switch (line.kind) {
    case 'live':
      return 'Prices are arriving in real time.'
    case 'waiting':
      return 'Waiting for the first price.'
    case 'polling':
      return line.stale
        ? `No price since ${clockSeconds.format(new Date(line.at))}. The real-time feed is reconnecting and the quotes standing in for it have stopped too.`
        : 'The real-time feed is reconnecting. Prices come from a quote every few seconds until it is back.'
    case 'connecting':
      if (!line.again) return 'Connecting to real-time prices.'
      return line.at === null
        ? 'The real-time feed dropped and is reconnecting.'
        : `The real-time feed dropped and is reconnecting. Prices shown are from ${clockSeconds.format(new Date(line.at))}.`
    case 'delayed':
      return line.stale
        ? `No price update since ${clockSeconds.format(new Date(line.at))}. The last price shown may be out of date.`
        : `Prices shown are from ${clockSeconds.format(new Date(line.at))}.`
    case 'closed':
      return status.opensAt === null
        ? 'Prices are from the last session.'
        : `Opens ${opensAtWords(status.opensAt, now)}. Prices are from the last session.`
  }
}

/**
 * When a closed market opens next, after the word "Opens": "at 09:15" later
 * the same day, "Thu 09:15" on another. India time, whatever the phone's zone.
 */
export function opensAtWords(opensAt: number, now = Date.now()): string {
  const opens = new Date(opensAt * 1000)
  const sameDay =
    new Date(now).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' }) ===
    opens.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata' })
  return sameDay ? `at ${clock.format(opens)}` : dayAndClock.format(opens).replace(', ', ' ')
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

/** When the figures behind a line were last updated, for the lines that say so. */
const ageOf = (line: FeedLine): number | null => ('at' in line ? line.at : null)

/** Longest single wait, so a sleeping machine is caught up within this. */
const MAX_WAIT_MS = 30 * 60_000

/** Follows one chart's prices and says when what its feed line says changes. */
export class LiveStatusTracker {
  private readonly opts: LiveStatusTrackerOptions
  private exchange: string
  private calendar: StatusCalendar | null = null
  private transport: LiveTransport = 'connecting'
  private lastTickAt: number | null = null
  private lastUpdateAt: number | null = null
  private lastTickTime: number | null = null
  /** The newest trade time a stream tick carried, in UTC seconds. */
  private lastTradeTime: number | null = null
  /** Wall clock, in milliseconds, of the last stream tick whose trade time moved on: trades flowing. */
  private flowAt: number | null = null
  private timer: unknown = null
  private shown: FeedLine | null = null
  private destroyed = false

  constructor(options: LiveStatusTrackerOptions) {
    this.opts = options
    this.exchange = options.exchange
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now()
  }

  /** A new instrument, just loaded from history: nothing has ticked for it yet. */
  reset(exchange: string): void {
    this.exchange = exchange
    this.lastTickAt = null
    this.lastTickTime = null
    this.lastTradeTime = null
    this.flowAt = null
    this.lastUpdateAt = this.now()
    this.update()
  }

  setCalendar(calendar: StatusCalendar | null): void {
    this.calendar = calendar
    this.update()
  }

  setTransport(transport: LiveTransport): void {
    if (transport === this.transport) return
    // While quotes are answering, another failed attempt to reconnect the
    // stream changes nothing a trader can see: still delayed. Only the stream
    // coming back ends the fallback.
    if (transport === 'connecting' && this.transport === 'polling') return
    this.transport = transport
    this.update()
  }

  /**
   * A price arrived. `time` is the exchange's time for it, in UTC seconds,
   * when known; `fromStream` is false for a quote request standing in for the
   * stream, which updates the figures but is never real-time. `tradeTime` is
   * the time of the last trade where the frame carried one: a newer trade
   * within a minute of the one before is the market trading.
   */
  noteTick(time?: number, fromStream = true, tradeTime?: number | null): void {
    const now = this.now()
    this.lastUpdateAt = now
    if (fromStream) this.lastTickAt = now
    if (typeof time === 'number' && Number.isFinite(time)) this.lastTickTime = time
    if (fromStream && typeof tradeTime === 'number' && Number.isFinite(tradeTime)) {
      const last = this.lastTradeTime
      if (last !== null && tradeTime > last && tradeTime - last <= FLOW_GAP_SEC) this.flowAt = now
      if (last === null || tradeTime > last) this.lastTradeTime = tradeTime
    }
    // Ticks arrive several times a second; while real-time, the timer already
    // aimed at the moment they would be too old is re-aimed when it fires.
    if (this.shown?.kind !== 'live' || this.timer === null) this.update()
  }

  /** Trades have been flowing within the stale window: the market is open, whatever the calendar says. */
  private flowing(now: number, staleMs: number): boolean {
    return this.flowAt !== null && now - this.flowAt <= staleMs
  }

  status(): LiveStatus {
    const now = this.now()
    const staleMs = staleAfterMs(this.exchange)
    const calendarOpen = this.calendar ? this.calendar.sessionAt(now / 1000) !== null : null
    const marketOpen = this.flowing(now, staleMs) ? true : calendarOpen
    const line = feedLine({
      now,
      marketOpen,
      lastTickAt: this.lastTickAt,
      lastUpdateAt: this.lastUpdateAt,
      connected: this.transport === 'live',
      polling: this.transport === 'polling',
      staleMs,
    })
    let opensAt: number | null = null
    if (line.kind === 'closed' && this.calendar) {
      const next = this.calendar.sessionFrom?.(now / 1000)
      opensAt = next ? next.open : null
    }
    return {
      line,
      lastTickAt: this.lastTickAt,
      lastUpdateAt: this.lastUpdateAt,
      lastTickTime: this.lastTickTime,
      staleAfterMs: staleMs,
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
    // A new state, or new figures behind the same one: a quote answering
    // every few seconds while the stream is down moves the age a marker shows
    // ("Delayed · updated 2s ago") without changing what it says.
    if (
      !this.shown ||
      !sameFeedState(this.shown, status.line) ||
      ageOf(this.shown) !== ageOf(status.line)
    ) {
      this.shown = status.line
      this.opts.onChange(status)
    }
    this.aim(status)
  }

  /** One timer, at the next instant the line can change without an event. */
  private aim(status: LiveStatus): void {
    this.clearTimer()
    const now = this.now()
    const waits: number[] = []
    if (status.line.kind !== 'closed') {
      // Real-time ends when the last tick is too old; a figure turns stale
      // when the last update is; trades stop counting as a market open once
      // they stop flowing.
      for (const at of [this.lastTickAt, this.lastUpdateAt, this.flowAt])
        if (at !== null) waits.push(at + status.staleAfterMs - now + 1)
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
