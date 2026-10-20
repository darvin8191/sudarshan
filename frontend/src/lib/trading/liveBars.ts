/**
 * The forming candle, built from ticks.
 *
 * One module for every chart that draws a live bar: each pane of the /trading
 * terminal and the phone chart. Both feed it the same ticks and both read the
 * same bars back, so the two can never disagree about what the current candle
 * is. Nothing here touches a timer, the network or the page; time is always an
 * argument, which is what lets a test walk it across a boundary at will.
 *
 * What it adds to a plain tick-to-candle fold:
 *
 * - **A bar opens on the clock.** At each boundary of an intraday interval the
 *   next bar opens at the last traded price with no volume, even when no tick
 *   has come, but only while the exchange's session is open. Such a bar has
 *   seen no trade, so the first tick into it becomes its open, high, low and
 *   close. A bar nobody traded never stays behind: at the next boundary it
 *   moves on to the new interval, so at most one flat bar is drawn, at the
 *   tail, as broker history leaves no candle for a minute with no trade.
 * - **Day, week and month bars are live.** Today's, this week's and this
 *   month's bar take every tick, bucketed by the exchange's own calendar in
 *   its own zone rather than by a fixed count of seconds. A week or month bar
 *   is history's bar for the period, which ends with yesterday, with today's
 *   own bar laid on top.
 * - **Volume comes from the day's running total when the tick carries it.**
 *   A bar's volume is the total now less the total when the bar began; a day
 *   bar's is the total itself. The total starts over on a new IST day, and a
 *   lower reading inside a day (an older quote, a pre-open update still
 *   carrying the last session's trade) is ignored: the day's highest stands.
 *   Without a total a last traded quantity is summed, and without either the
 *   bar keeps what history said until the next re-sync.
 * - **No bar opens outside a session, unless the market is plainly trading.**
 *   A tick that would start a new bar while the calendar says the exchange is
 *   shut (a snapshot at night, the pre-open auction) moves no bar. Trades
 *   whose exchange times keep advancing do open bars whatever the calendar
 *   says, so a special session the holiday table does not list is still
 *   drawn; only the clock still needs the calendar.
 * - **A tick goes to the bar its time belongs to.** Where the feed stamps a
 *   tick with the exchange's last-trade time, a book update after a boundary
 *   still carries the last trade before it. Such a late tick updates the bar
 *   that trade belongs to and never the forming bar, and the only trades of an
 *   interval that had no bar give that interval one.
 * - **A quote standing in for the stream moves prices, not counts.** It is
 *   never the first count a bar measures from, and its older readings are
 *   ignored like any other.
 * - **History merges by bucket.** A history snapshot and the forming bar meet
 *   in `mergeSnapshot`, which keeps one bar per bucket whatever time stamp each
 *   side gave it.
 */
import {
  type Bar,
  startOfZonedDay,
  startOfZonedMonth,
  startOfZonedWeek,
  utcSecondsToZonedParts,
  zonedDayIndex,
  zonedWeekIndex,
} from 'openalgo-charts'

/** The zone every Indian exchange keeps its calendar in. */
export const LIVE_ZONE = 'Asia/Kolkata'

/**
 * Two trades this close together, in seconds, with the later one newer, are a
 * market trading, whatever the calendar says.
 */
export const FLOW_GAP_SEC = 60

/** One session window, in UTC seconds, the close exclusive. */
export interface SessionWindow {
  open: number
  close: number
}

/**
 * The part of a session calendar the live bar reads. The engine's
 * `SessionCalendar` satisfies it, and a test can hand in two lines instead.
 */
export interface SessionHours {
  /** The regular session in force at an instant, or null while the exchange is shut. */
  sessionAt(utcSeconds: number): SessionWindow | null
  /** The session in force at an instant, or else the next one to open. */
  sessionFrom?(utcSeconds: number): SessionWindow | null
  /** The part of the day an instant falls in: 'pre' for the pre-open, 'regular', 'closed' and so on. */
  phaseAt?(utcSeconds: number): string
}

/** One price update, in the shape every feed is reduced to before it reaches a bar. */
export interface LiveTick {
  /** UTC seconds the price belongs to: the exchange's time when the feed sends one. */
  time: number
  price: number
  /** The day's cumulative traded quantity, when the feed carries it. */
  dayVolume?: number
  /** The quantity of the last trade, when the feed carries that instead. */
  ltq?: number
  /** Open interest at this tick. A level, so it replaces rather than adds. */
  oi?: number
  /**
   * A quote request standing in for the stream. It moves the price and the
   * bar, but its count never sets where a bar's volume starts, and at the
   * last traded price with no new count it is not a trade at all.
   */
  polled?: boolean
  /**
   * The exchange's time of the last trade, in UTC seconds, when the frame
   * carried one. A frame's own stamp moves on whether anything traded or not;
   * only trade times that keep advancing show a market trading.
   */
  tradeTime?: number | null
}

/** A bar the live path changed, for the host to draw. */
export interface LiveBarUpdate {
  bar: Bar
  /** This update opened a bucket rather than changing the one already open. */
  isNew: boolean
  /**
   * The bar's open, high, low and volume cover only what this module saw: it
   * opened the bucket without having watched the bucket before it, so trades
   * between the true open and the first tick seen may be missing. History's
   * bar for the same bucket corrects it through `reconcile`.
   */
  provisional: boolean
  /** Opened by the clock, with no trade yet: a flat bar at the last traded price. */
  placeholder: boolean
  /**
   * A closed bar a late trade changed, or created for an interval that had
   * none. Put it in time order (`insertLiveBar`) and draw it again too.
   */
  previous?: Bar
  /**
   * The time of a flat bar nobody traded that this one takes the place of.
   * Take that bar out (`removeLiveBar`), then draw this one.
   */
  replaces?: number
}

/** What the countdown beside the last price shows. */
export type LiveCountdown =
  /** The forming bar closes inside the session: count down to it. */
  | { kind: 'bar' }
  /** The session closes before the bar would: say when, instead of a countdown that overshoots. */
  | { kind: 'closes'; at: number }
  /** No bar is coming, or none is cut by the clock: show nothing. */
  | { kind: 'off' }

/** How an interval cuts time into bars. */
export type LiveBucketing =
  | { kind: 'intraday'; seconds: number }
  | { kind: 'calendar'; unit: 'day' | 'week' | 'month' }

/** The bucketing for an interval code, or null for one the live bar cannot build. */
export function liveBucketing(interval: string): LiveBucketing | null {
  const intraday = /^(\d+)([smh])$/.exec(interval)
  if (intraday) {
    const n = Number(intraday[1])
    if (!(n > 0)) return null
    const unit = intraday[2]
    return { kind: 'intraday', seconds: unit === 's' ? n : unit === 'm' ? n * 60 : n * 3600 }
  }
  // Upper case M is a month and lower case m a minute, as everywhere else in the app.
  if (interval === 'D' || interval === '1D' || interval === '1d')
    return { kind: 'calendar', unit: 'day' }
  if (interval === 'W' || interval === '1W' || interval === '1w')
    return { kind: 'calendar', unit: 'week' }
  if (interval === 'M' || interval === '1M') return { kind: 'calendar', unit: 'month' }
  return null
}

/**
 * Whether today's session has opened by `now`, in UTC seconds: what a quote's
 * figures for the day then describe. On a day the exchange is shut, or before
 * its open, a quote still carries the last session's, which history holds.
 */
export function tradedToday(session: SessionHours, now: number, zone = LIVE_ZONE): boolean {
  if (session.sessionAt(now)) return true
  const first = session.sessionFrom?.(startOfZonedDay(now, zone))
  return (
    !!first && first.open <= now && zonedDayIndex(first.open, zone) === zonedDayIndex(now, zone)
  )
}

/** A count the feed reported: a finite number that is not negative. */
function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function sameBar(a: Bar, b: Bar): boolean {
  return (
    a.time === b.time &&
    a.open === b.open &&
    a.high === b.high &&
    a.low === b.low &&
    a.close === b.close &&
    (a.volume ?? 0) === (b.volume ?? 0)
  )
}

/** `a mod b` that stays positive for a negative `a`. */
function mod(a: number, b: number): number {
  return ((a % b) + b) % b
}

/**
 * A week or month bar: history's bar for the period, which ends with
 * yesterday, with today's own bar on top. History keeps the open; the close
 * is today's; the volumes add.
 */
function stack(history: Bar | null, today: Bar, time: number): Bar {
  if (!history) return { ...today, time }
  const bar: Bar = {
    time,
    open: history.open,
    high: Math.max(history.high, today.high),
    low: Math.min(history.low, today.low),
    close: today.close,
    volume: (history.volume ?? 0) + (today.volume ?? 0),
  }
  const oi = today.oi ?? history.oi
  if (oi !== undefined) bar.oi = oi
  return bar
}

export interface LiveBarOptions {
  interval: string
  /**
   * A bar time from the broker's history. Intraday buckets are congruent with
   * it, which keeps an hourly bar opening at the session's 09:15 rather than
   * at a round hour of the epoch. Defaults to the first bar seeded.
   */
  anchorSec?: number
  /** The exchange's hours. Without them no bar opens on the clock and no tick is refused. */
  session?: SessionHours | null
  /** The calendar's zone, for day, week and month buckets. */
  zone?: string
}

/**
 * Builds the forming bar from ticks and the clock.
 *
 * Shaped like the engine's own candle builder (`seed`, `current`, `onTick`,
 * `reconcile`, `bucketStart`) so the hosts read the same way, with the clock,
 * the calendar buckets, the volume rules and the snapshot merge added.
 */
export class LiveBarBuilder {
  readonly interval: string
  readonly bucketing: LiveBucketing
  private readonly zone: string
  private anchor: number | null
  private session: SessionHours | null
  private cur: Bar | null = null
  /** The current bar is a flat bar the clock opened, and no trade has reached it yet. */
  private empty = false
  /** The current bar has taken a tick or the clock: it is ahead of history. */
  private live = false
  /** A stream tick has been folded since the last seed, so the next bucket's first tick is its open. */
  private streamed = false
  private provisional = false
  /** The day's count when the current bar began; null while not known. */
  private dayBase: number | null = null
  /** The highest count the day has reached, and the IST day it belongs to. */
  private lastDay: number | null = null
  private lastDayIndex: number | null = null
  /** Whether `lastDay` is where the next bar's count starts: false after a gap in the stream. */
  private lastDayTrusted = false
  /** Seconds after local midnight at which history stamps day, week and month bars. */
  private stampOffset = 0
  /** Tick time of the last stream tick that carried a day count. */
  private dayCountAt: number | null = null
  /**
   * The last bar handed to the host. A history owner that has not heard from
   * history yet hands exactly this back, and that is not news.
   */
  private echo: Bar | null = null
  /** Tick time of the newest trade folded into the current bar: its close. */
  private curCloseAt = Number.NEGATIVE_INFINITY
  /**
   * The traded bar before the current one, which a late trade may still
   * reach. History's copy of it may predate its last trades, so it stays
   * until the next bar is retired.
   */
  private prev: Bar | null = null
  /** The day's count when `prev` began, when known. */
  private prevBase: number | null = null
  /** Tick time of the newest trade folded into `prev`. */
  private prevCloseAt = Number.NEGATIVE_INFINITY
  /** The newest trade seen, by tick time: what a bar the clock opens is flat at. */
  private lastTradeAt = Number.NEGATIVE_INFINITY
  private lastTradePrice: number | null = null
  /** The newest trade time a stream tick carried: what tells trades flowing from a book moving. */
  private lastTradeStamp: number | null = null
  /** Week and month bars: history's bar for the period, which ends with yesterday. */
  private periodHistory: Bar | null = null
  /**
   * The IST day history was read on, when the host says: a week or month bar
   * from history holds every day before it, so a tick from one of those days
   * (a snapshot at the weekend, a pre-open frame still carrying yesterday's
   * last trade) is already counted.
   */
  private historyDay: number | null = null
  /** Week and month bars: today's own bar, laid on top of history's. */
  private today: Bar | null = null
  private todayIndex: number | null = null
  private todayCloseAt = Number.NEGATIVE_INFINITY

  constructor(options: LiveBarOptions) {
    const bucketing = liveBucketing(options.interval)
    if (!bucketing) throw new Error(`No live bar for the interval ${options.interval}`)
    this.interval = options.interval
    this.bucketing = bucketing
    this.zone = options.zone ?? LIVE_ZONE
    this.anchor = options.anchorSec ?? null
    this.session = options.session ?? null
  }

  /** Whether an interval has a live bar at all. */
  static supports(interval: string): boolean {
    return liveBucketing(interval) !== null
  }

  /** Give the builder the exchange's hours, or take them away. */
  setSession(session: SessionHours | null): void {
    this.session = session
  }

  hasSession(): boolean {
    return this.session !== null
  }

  /**
   * Continue from the last bar history holds. The next tick in the same bucket
   * folds into it; a tick in a later bucket opens a provisional bar, because
   * nothing was watching the bucket in between. `readAt` is when history was
   * read, in UTC seconds, which tells a week or month bar which days it holds.
   */
  seed(bar: Bar, readAt?: number): void {
    this.noteRead(readAt)
    this.cur = { ...bar }
    this.echo = null
    this.empty = false
    this.live = false
    this.streamed = false
    this.provisional = false
    this.dayBase = null
    this.lastDayTrusted = false
    this.prev = null
    this.prevBase = null
    this.prevCloseAt = Number.NEGATIVE_INFINITY
    // History's close is at least as new as its bar's start.
    this.curCloseAt = bar.time
    this.lastTradeAt = bar.time
    this.lastTradePrice = bar.close
    this.periodHistory = this.isPeriodBar() ? { ...bar } : null
    this.today = null
    this.todayIndex = null
    this.todayCloseAt = Number.NEGATIVE_INFINITY
    if (this.anchor === null) this.anchor = bar.time
    if (this.bucketing.kind === 'calendar')
      this.stampOffset = bar.time - startOfZonedDay(bar.time, this.zone)
  }

  /**
   * The stream came back after a break. Ticks were missed: the next bucket's
   * first tick is not its open, and the last day count is not where the next
   * bar's count starts. The bar already open keeps its count, because a
   * cumulative figure includes whatever traded during the gap.
   */
  resync(): void {
    this.streamed = false
    this.lastDayTrusted = false
  }

  current(): Bar | null {
    return this.cur === null ? null : { ...this.cur }
  }

  isProvisional(): boolean {
    return this.cur !== null && this.provisional
  }

  /** The current bar was opened by the clock and has not traded yet. */
  isPlaceholder(): boolean {
    return this.cur !== null && this.empty
  }

  /** The current bar has taken a tick or the clock since history last set it. */
  isLive(): boolean {
    return this.cur !== null && this.live
  }

  /** Whether any stream tick so far carried the day's cumulative volume. */
  hasTickVolume(): boolean {
    return this.dayCountAt !== null
  }

  /** Bucket start (the bar's time) for an instant. */
  bucketStart(time: number): number {
    if (this.bucketing.kind === 'calendar') {
      if (this.cur && this.periodOf(time) === this.periodOf(this.cur.time)) return this.cur.time
      return this.periodStart(time) + this.stampOffset
    }
    const seconds = this.bucketing.seconds
    let anchor = this.anchor ?? 0
    // History sets the alignment. Where it would put the session's open inside
    // a bucket (an interval that does not divide the day), the session wins, so
    // every day's first bar opens with the market.
    const session = this.session?.sessionAt(time)
    if (session && mod(session.open - anchor, seconds) !== 0) anchor = session.open
    return anchor + Math.floor((time - anchor) / seconds) * seconds
  }

  /**
   * When the clock should next look at this bar, in UTC seconds: the next
   * boundary inside a session, or the next session's open. Null for day, week
   * and month bars, and whenever the hours are not known.
   */
  nextClockAt(now: number): number | null {
    if (this.bucketing.kind !== 'intraday' || !this.session) return null
    const session = this.session.sessionAt(now) ?? this.session.sessionFrom?.(now) ?? null
    if (!session) return null
    if (now < session.open) return session.open
    const next = this.bucketStart(now) + this.bucketing.seconds
    if (next < session.close) return next
    return this.session.sessionFrom?.(session.close)?.open ?? null
  }

  /**
   * What the countdown to the forming bar's close shows at an instant: the
   * bar's own close while that falls inside the session; when the session
   * closes, where that comes first (an hourly bar that starts at 15:15 closes
   * with the session at 15:30, not at 16:15); and nothing outside a session,
   * for a day, week or month bar, or without hours.
   */
  countdown(now: number): LiveCountdown {
    if (this.bucketing.kind !== 'intraday' || !this.session) return { kind: 'off' }
    const session = this.session.sessionAt(now)
    if (!session) return { kind: 'off' }
    const end = this.bucketStart(now) + this.bucketing.seconds
    return end > session.close ? { kind: 'closes', at: session.close } : { kind: 'bar' }
  }

  /**
   * Fold one tick. Returns the bar it changed or opened, or null when the
   * tick moves no bar: an unusable price, a bar that would open while the
   * exchange is shut, a quote that shows no trade, or a late tick that
   * changes nothing (a book update carrying a trade already counted).
   */
  onTick(tick: LiveTick): LiveBarUpdate | null {
    const price = tick.price
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(tick.time)) return null
    if (tick.polled && !this.tradedSince(tick)) return null
    const flowing = this.flowing(tick)
    this.noteStamp(tick)
    const bucket = this.bucketStart(tick.time)
    const cur = this.cur
    if (cur && bucket < cur.time) return this.onLateTick(tick, bucket)
    if (cur && bucket === cur.time) {
      if (!this.isPeriodBar()) this.fold(cur, tick)
      else if (!this.foldToday(tick)) return null
      this.noteTrade(tick)
      if (!tick.polled) this.streamed = true
      this.live = true
      const bar = this.cur as Bar
      this.echo = { ...bar }
      return { bar: { ...bar }, isNew: false, provisional: this.provisional, placeholder: false }
    }
    if (this.session && !this.session.sessionAt(tick.time) && !flowing) {
      // A trade while the calendar says the exchange is shut opens no bar (the
      // pre-open auction, a snapshot at night). Its price is the one the
      // session's first bar starts flat at, and its count is today's.
      this.takeCount(tick)
      this.noteTrade(tick)
      return null
    }
    // The open of a day, week or month bar, and of a session's first bar,
    // includes the opening auction, which a feed does not stream as ticks, and
    // a quote standing in for the stream samples a bar rather than opening it:
    // history's open is the true one.
    const provisional =
      cur === null ||
      !this.streamed ||
      tick.polled === true ||
      this.bucketing.kind === 'calendar' ||
      this.opensSession(bucket)
    // A flat bar nobody traded gives its place to this one rather than staying behind.
    const replaces = cur && this.empty ? cur.time : undefined
    if (cur && !this.empty) this.retire(cur)
    if (this.isPeriodBar()) {
      this.periodHistory = null
      this.today = null
      this.todayIndex = null
      this.cur = { time: bucket, open: price, high: price, low: price, close: price, volume: 0 }
      this.foldToday(tick)
    } else {
      const bar: Bar = {
        time: bucket,
        open: price,
        high: price,
        low: price,
        close: price,
        volume: this.openingVolume(bucket, tick),
      }
      if (isCount(tick.oi)) bar.oi = tick.oi
      this.cur = bar
    }
    const bar = this.cur as Bar
    this.curCloseAt = tick.time
    this.noteTrade(tick)
    this.empty = false
    this.live = true
    if (!tick.polled) this.streamed = true
    this.provisional = provisional
    this.echo = { ...bar }
    const update: LiveBarUpdate = { bar: { ...bar }, isNew: true, provisional, placeholder: false }
    if (replaces !== undefined) update.replaces = replaces
    return update
  }

  /**
   * Today's own bar as a quote gives it (open, high, low, last price and the
   * day's count), for a week or month bar whose history ends with yesterday:
   * today goes on top with its true open and extremes, rather than only what
   * has ticked since the chart opened. Returns the bar to draw, or null for
   * another timeframe or another period.
   */
  foldDay(day: Bar): LiveBarUpdate | null {
    const cur = this.cur
    if (!this.isPeriodBar() || !cur) return null
    if (this.periodOf(day.time) !== this.periodOf(cur.time)) return null
    const index = zonedDayIndex(day.time, this.zone)
    if (this.todayIndex !== null && index < this.todayIndex) return null
    if (this.historyDay !== null && index < this.historyDay) return null
    if (this.today === null || this.todayIndex !== index) {
      if (this.today) this.periodHistory = stack(this.periodHistory, this.today, cur.time)
      this.today = { ...day, time: startOfZonedDay(day.time, this.zone) }
      this.todayIndex = index
      this.todayCloseAt = day.time
    } else {
      const today = this.today
      today.open = day.open
      today.high = Math.max(today.high, day.high)
      today.low = Math.min(today.low, day.low)
      if (isCount(day.volume)) today.volume = Math.max(today.volume ?? 0, day.volume)
      if (day.time >= this.todayCloseAt) {
        today.close = day.close
        this.todayCloseAt = day.time
      }
    }
    const bar = stack(this.periodHistory, this.today, cur.time)
    this.cur = bar
    this.live = true
    this.echo = { ...bar }
    return { bar: { ...bar }, isNew: false, provisional: this.provisional, placeholder: false }
  }

  /**
   * A tick whose time belongs to an earlier bucket than the forming bar: the
   * last trade before a boundary, delivered after the next bar opened, or a
   * book update that still carries it. It updates the traded bar before the
   * forming one when it belongs there. The only trades of an interval that
   * has no bar (the clock's flat bar moved on without them) give that
   * interval one, all at the trade's price. The forming bar takes none of it;
   * a bar the clock opened only follows the last traded price.
   */
  private onLateTick(tick: LiveTick, bucket: number): LiveBarUpdate | null {
    const cur = this.cur as Bar
    const read = this.takeCount(tick)
    // A late trade's count is the reading of its own moment: it may be below
    // the day's highest, and is still right for the bar the trade belongs to.
    const count = read ? read.count : this.lateCount(tick)
    let previous: Bar | undefined
    const prev = this.prev
    if (prev && bucket === prev.time) {
      const next: Bar = { ...prev }
      if (tick.price > next.high) next.high = tick.price
      if (tick.price < next.low) next.low = tick.price
      if (tick.time >= this.prevCloseAt) {
        next.close = tick.price
        this.prevCloseAt = tick.time
      }
      if (count !== null && this.prevBase !== null)
        next.volume = Math.max(next.volume ?? 0, count - this.prevBase)
      if (isCount(tick.oi)) next.oi = tick.oi
      if (!sameBar(next, prev) || next.oi !== prev.oi) {
        this.prev = next
        previous = { ...next }
      }
    } else if (
      this.empty &&
      !tick.polled &&
      !this.isPeriodBar() &&
      bucket > (prev?.time ?? Number.NEGATIVE_INFINITY) &&
      this.inSession(tick.time)
    ) {
      const price = tick.price
      const bar: Bar = {
        time: bucket,
        open: price,
        high: price,
        low: price,
        close: price,
        volume: count !== null && this.dayBase !== null ? Math.max(0, count - this.dayBase) : 0,
      }
      if (isCount(tick.oi)) bar.oi = tick.oi
      this.prev = bar
      this.prevBase = this.dayBase
      this.prevCloseAt = tick.time
      previous = { ...bar }
    }
    this.noteTrade(tick)
    let changed = false
    if (this.empty) {
      const level = this.lastTradePrice
      if (level !== null && cur.close !== level) {
        cur.open = level
        cur.high = level
        cur.low = level
        cur.close = level
        changed = true
      }
      // Everything counted so far today traded before this bar. The session's
      // first bar is the exception: it takes the whole day, pre-open included.
      if (
        count !== null &&
        !this.opensSession(cur.time) &&
        zonedDayIndex(tick.time, this.zone) === zonedDayIndex(cur.time, this.zone)
      )
        this.dayBase = Math.max(this.dayBase ?? count, count)
    }
    if (!tick.polled) this.streamed = true
    if (!previous && !changed) return null
    this.echo = { ...cur }
    const update: LiveBarUpdate = {
      bar: { ...cur },
      isNew: false,
      provisional: this.provisional,
      placeholder: this.empty,
    }
    if (previous) update.previous = previous
    return update
  }

  /** A late stream tick's own count, from the same IST day as the day's highest. */
  private lateCount(tick: LiveTick): number | null {
    const count = tick.dayVolume
    if (!isCount(count) || tick.polled) return null
    if (this.lastDayIndex !== null && zonedDayIndex(tick.time, this.zone) !== this.lastDayIndex)
      return null
    return count
  }

  /** The current bar is giving way to the next: keep it while a late trade may still reach it. */
  private retire(cur: Bar): void {
    this.prev = { ...cur }
    this.prevBase = this.dayBase
    this.prevCloseAt = this.curCloseAt
  }

  /** Remember the newest trade by its time, whichever bar it went to. */
  private noteTrade(tick: LiveTick): void {
    if (tick.time < this.lastTradeAt) return
    this.lastTradeAt = tick.time
    this.lastTradePrice = tick.price
  }

  /** Whether a quote shows trading since the last trade seen: a new price, or a higher count. */
  private tradedSince(tick: LiveTick): boolean {
    if (tick.price !== this.lastTradePrice) return true
    return isCount(tick.dayVolume) && this.lastDay !== null && tick.dayVolume > this.lastDay
  }

  /**
   * Trades whose exchange times keep advancing, a minute or less apart: the
   * market is trading, whatever the calendar says (a special session the
   * holiday table does not list). Only a stream tick's trade time counts: a
   * frame's own stamp moves on with nothing traded. The pre-open does not:
   * its auction is one trade, and its book moves without trading.
   */
  private flowing(tick: LiveTick): boolean {
    const at = tick.tradeTime
    if (tick.polled || typeof at !== 'number' || !Number.isFinite(at)) return false
    if (this.session?.phaseAt?.(at) === 'pre') return false
    const last = this.lastTradeStamp
    return last !== null && at > last && at - last <= FLOW_GAP_SEC
  }

  /** Remember the newest trade time a stream tick carried. */
  private noteStamp(tick: LiveTick): void {
    const at = tick.tradeTime
    if (tick.polled || typeof at !== 'number' || !Number.isFinite(at)) return
    if (this.lastTradeStamp === null || at > this.lastTradeStamp) this.lastTradeStamp = at
  }

  private inSession(time: number): boolean {
    return !this.session || this.session.sessionAt(time) !== null
  }

  /**
   * Open the next intraday bar at a boundary when no tick has. Flat at the
   * last traded price, no volume. Only inside a session. A traded bar gives
   * way only to the bucket straight after it or to the session's first (a
   * later one means the clock slept through some, whose prices are not
   * known). A flat bar nobody traded moves on to the new bucket instead of
   * staying behind.
   */
  onClock(now: number): LiveBarUpdate | null {
    if (this.bucketing.kind !== 'intraday' || !this.session || !this.cur) return null
    const session = this.session.sessionAt(now)
    if (!session) return null
    const seconds = this.bucketing.seconds
    const bucket = this.bucketStart(now)
    const cur = this.cur
    if (bucket <= cur.time || bucket < session.open || bucket >= session.close) return null
    const opensSession = session.open >= bucket && session.open < bucket + seconds
    if (!this.empty && bucket !== cur.time + seconds && !opensSession) return null
    // Flat at the last traded price: the bar's own close, or a pre-open trade
    // before the session's first bar.
    const level = this.lastTradePrice ?? cur.close
    const bar: Bar = { time: bucket, open: level, high: level, low: level, close: level, volume: 0 }
    // A session's first bar opens at its auction, which history has and ticks do not.
    const provisional = !this.streamed || opensSession
    const replaces = this.empty ? cur.time : undefined
    if (!this.empty) this.retire(cur)
    if (opensSession) this.dayBase = 0
    else {
      // Where the day's count stands, if it is today's and nothing was missed;
      // and where the flat bar moving on started, unless that was the
      // session's first bar, which alone takes the whole day.
      const today = zonedDayIndex(bucket, this.zone)
      const known = this.lastDayTrusted && this.lastDayIndex === today ? this.lastDay : null
      const kept = replaces !== undefined && !this.opensSession(cur.time) ? this.dayBase : null
      this.dayBase = kept === null ? known : known === null ? kept : Math.max(kept, known)
    }
    this.cur = bar
    this.curCloseAt = Number.NEGATIVE_INFINITY
    this.empty = true
    this.live = true
    this.provisional = provisional
    this.echo = { ...bar }
    const update: LiveBarUpdate = { bar: { ...bar }, isNew: true, provisional, placeholder: true }
    if (replaces !== undefined) update.replaces = replaces
    return update
  }

  /**
   * Take history's bar for the bucket now forming. A provisional bar takes
   * history's open, since its own is only the first tick it saw. Both sides
   * observed real prices, so the extremes are the union, and the larger
   * volume wins, because volume inside a bar only grows. The close stays with
   * the ticks: history is a snapshot taken before its request went out, and a
   * price that steps back on every repair is the one thing a live chart must
   * not do. A bar the clock opened has seen no trade, so history's replaces it
   * whole. A week or month bar takes history's bar for the period as the part
   * up to yesterday, with today's own on top.
   *
   * Returns the reconciled bar, or null when the builder holds no bar for
   * that bucket.
   */
  reconcile(authoritative: Bar): Bar | null {
    const cur = this.cur
    if (!cur || authoritative.time !== cur.time) return null
    // The builder's own last bar, handed back by a history owner that has not
    // heard from history since: a provisional bar stays provisional and a
    // placeholder stays untraded.
    if (this.echo && sameBar(this.echo, authoritative)) return { ...cur }
    if (this.isPeriodBar()) {
      this.periodHistory = { ...authoritative }
      // A day history has caught up with is history's to count now.
      if (
        this.todayIndex !== null &&
        this.historyDay !== null &&
        this.todayIndex < this.historyDay
      ) {
        this.today = null
        this.todayIndex = null
      }
      this.cur = this.today
        ? stack(this.periodHistory, this.today, authoritative.time)
        : { ...authoritative }
      this.provisional = false
      return { ...this.cur }
    }
    if (this.empty) {
      this.cur = { ...authoritative }
      this.empty = false
    } else {
      const merged: Bar = {
        ...cur,
        high: Math.max(cur.high, authoritative.high),
        low: Math.min(cur.low, authoritative.low),
      }
      if (cur.volume === undefined && authoritative.volume === undefined) delete merged.volume
      else merged.volume = Math.max(cur.volume ?? 0, authoritative.volume ?? 0)
      if (this.provisional) merged.open = authoritative.open
      this.cur = merged
    }
    this.provisional = false
    // The day's count carries on from the reconciled volume.
    const volume = this.cur.volume
    if (this.dayBase !== null && this.lastDay !== null && volume !== undefined)
      this.dayBase = this.lastDay - volume
    return { ...this.cur }
  }

  /**
   * One history snapshot with the live bars merged in, one bar per bucket.
   *
   * - The bar history holds for the forming bucket is reconciled with the
   *   builder's. For day, week and month bars that is matched by period, and
   *   where history stamped the period differently from the builder, history's
   *   stamp is kept and the builder moves to it: two bars for one week is the
   *   duplicate this exists to prevent.
   * - The traded bar before the forming one takes the union with history's
   *   copy, which may predate its last trades (a repair asked a moment after
   *   the boundary), and a late trade can still reach it.
   * - A forming bar history does not have yet is put after its last bar.
   * - When the builder has seen nothing since history last set it, or history
   *   is already a bucket ahead, the builder follows history instead.
   *
   * `readAt` is when history was read, in UTC seconds, as for `seed`.
   */
  mergeSnapshot(bars: readonly Bar[], readAt?: number): Bar[] {
    this.noteRead(readAt)
    const out = bars.slice()
    const cur = this.cur
    if (!cur || !out.length) return out
    const prev = this.prev
    if (prev) {
      // History a moment behind has no copy of it yet: it stays drawn, before the forming bar.
      if (prev.time > out[out.length - 1].time && prev.time < cur.time) out.push({ ...prev })
      for (let i = out.length - 1; i >= 0 && out[i].time >= prev.time; i--) {
        if (out[i].time !== prev.time) continue
        const history = out[i]
        const merged: Bar = {
          ...prev,
          open: history.open,
          high: Math.max(prev.high, history.high),
          low: Math.min(prev.low, history.low),
          // The ticks' close where a trade inside the bar was seen.
          close: this.prevCloseAt > prev.time ? prev.close : history.close,
        }
        if (prev.volume === undefined && history.volume === undefined) delete merged.volume
        else merged.volume = Math.max(prev.volume ?? 0, history.volume ?? 0)
        if (merged.oi === undefined && history.oi !== undefined) merged.oi = history.oi
        this.prev = merged
        out[i] = { ...merged }
        break
      }
    }
    const last = out[out.length - 1]
    if (!this.live) {
      if (last.time >= cur.time) this.adopt(last)
      return out
    }
    if (this.bucketing.kind === 'calendar') this.mergePeriod(out, cur)
    else {
      let index = -1
      for (let i = out.length - 1; i >= 0 && out[i].time >= cur.time; i--) {
        if (out[i].time === cur.time) {
          index = i
          break
        }
      }
      if (index >= 0) out[index] = this.reconcile(out[index]) ?? out[index]
      else if (cur.time > last.time) out.push({ ...cur })
    }
    const newest = out[out.length - 1]
    if (this.cur && newest.time > this.cur.time) this.adopt(newest)
    return out
  }

  /** `mergeSnapshot` for day, week and month bars: one bar for the forming period. */
  private mergePeriod(out: Bar[], cur: Bar): void {
    const key = this.periodOf(cur.time)
    const same: number[] = []
    for (let i = out.length - 1; i >= 0; i--) {
      const period = this.periodOf(out[i].time)
      if (period < key) break
      if (period === key) same.unshift(i)
    }
    if (!same.length) {
      if (cur.time > out[out.length - 1].time) out.push({ ...cur })
      return
    }
    // History's own stamp for the period, where it has one beside the builder's.
    const authIndex = same.find((i) => out[i].time !== cur.time) ?? same[0]
    const authoritative = out[authIndex]
    if (this.cur && authoritative.time !== this.cur.time)
      this.cur = { ...this.cur, time: authoritative.time }
    const merged = this.reconcile(authoritative) ?? authoritative
    for (let i = same.length - 1; i >= 0; i--) {
      if (same[i] === authIndex) out[same[i]] = merged
      else out.splice(same[i], 1)
    }
  }

  /** Follow history's bar: nothing the builder holds is newer. */
  private adopt(bar: Bar): void {
    this.seed(bar)
  }

  /** History was read at this instant: its week or month bar holds the days before it. */
  private noteRead(readAt: number | undefined): void {
    if (typeof readAt !== 'number' || !Number.isFinite(readAt)) return
    const day = zonedDayIndex(readAt, this.zone)
    if (this.historyDay === null || day > this.historyDay) this.historyDay = day
  }

  /** Fold a tick into an intraday or day bar. */
  private fold(cur: Bar, tick: LiveTick): void {
    const price = tick.price
    if (this.empty) {
      // The first trade into a bar the clock opened is its open.
      cur.open = price
      cur.high = price
      cur.low = price
      this.empty = false
    } else {
      if (price > cur.high) cur.high = price
      if (price < cur.low) cur.low = price
    }
    // The close is the newest trade by time; one delivered out of order
    // widens the bar but does not take the close back.
    if (tick.time >= this.curCloseAt) {
      cur.close = price
      this.curCloseAt = tick.time
    }
    const volume = this.foldVolume(cur, tick)
    if (volume === undefined) delete cur.volume
    else cur.volume = volume
    // A level, not a flow: a stream tick without one leaves the bar without
    // one, which is honest where a carried-over figure would not be. A quote
    // that does not carry it changes nothing.
    if (isCount(tick.oi)) cur.oi = tick.oi
    else if (!tick.polled) delete cur.oi
  }

  /**
   * Week and month bars: fold a tick into today's own bar, which is laid on
   * history's bar for the period. A new day under the same period moves the
   * day that ended under history's part. A tick from a day history already
   * holds, or from before today's, changes nothing: returns false.
   */
  private foldToday(tick: LiveTick): boolean {
    const index = zonedDayIndex(tick.time, this.zone)
    if (this.todayIndex !== null && index < this.todayIndex) return false
    if (this.historyDay !== null && index < this.historyDay) return false
    const time = (this.cur as Bar).time
    if (this.today === null || this.todayIndex !== index) {
      if (this.today) this.periodHistory = stack(this.periodHistory, this.today, time)
      this.today = {
        time: startOfZonedDay(tick.time, this.zone),
        open: tick.price,
        high: tick.price,
        low: tick.price,
        close: tick.price,
        volume: 0,
      }
      this.todayIndex = index
      this.todayCloseAt = Number.NEGATIVE_INFINITY
    }
    const today = this.today
    if (tick.price > today.high) today.high = tick.price
    if (tick.price < today.low) today.low = tick.price
    if (tick.time >= this.todayCloseAt) {
      today.close = tick.price
      this.todayCloseAt = tick.time
    }
    const read = this.takeCount(tick)
    if (read) today.volume = Math.max(today.volume ?? 0, read.count)
    if (isCount(tick.oi)) today.oi = tick.oi
    this.cur = stack(this.periodHistory, today, time)
    return true
  }

  /** Whether an intraday bucket holds its session's open. */
  private opensSession(bucket: number): boolean {
    if (this.bucketing.kind !== 'intraday' || !this.session) return false
    const session = this.session.sessionAt(bucket)
    return (
      session !== null && session.open >= bucket && session.open < bucket + this.bucketing.seconds
    )
  }

  private isDayBar(): boolean {
    return this.bucketing.kind === 'calendar' && this.bucketing.unit === 'day'
  }

  /** A week or month bar: history's part up to yesterday, and today's on top. */
  private isPeriodBar(): boolean {
    return this.bucketing.kind === 'calendar' && this.bucketing.unit !== 'day'
  }

  /**
   * Read a tick's day count, the running total of the IST day its time falls
   * in. A new day starts the total over. Inside a day a lower count is an
   * older reading (a quote answered before the stream's last tick, a pre-open
   * update still carrying the last session's trade) and is not used: the
   * day's highest count stands. A quote never starts a day.
   */
  private takeCount(tick: LiveTick): { count: number; newDay: boolean } | null {
    const count = tick.dayVolume
    if (!isCount(count)) return null
    const index = zonedDayIndex(tick.time, this.zone)
    if (this.lastDayIndex !== null && index < this.lastDayIndex) return null
    const newDay = this.lastDayIndex !== null && index > this.lastDayIndex
    if (newDay && tick.polled) return null
    if (!newDay && this.lastDay !== null && count < this.lastDay) return null
    this.lastDayIndex = index
    this.lastDay = count
    this.lastDayTrusted = true
    if (!tick.polled) this.dayCountAt = tick.time
    return { count, newDay }
  }

  /** Volume for an intraday or day bar a tick opens. */
  private openingVolume(bucket: number, tick: LiveTick): number {
    const before = this.lastDay
    const beforeDay = this.lastDayIndex
    const trusted = this.lastDayTrusted
    // Everything traded today so far belongs to the day's bar and to the session's first.
    const wholeDay = this.isDayBar() || this.opensSession(bucket)
    const read = this.takeCount(tick)
    if (read) {
      let base: number
      if (wholeDay) base = 0
      else if (before !== null && !read.newDay && (trusted || tick.polled)) {
        // Where the last bar's count ended.
        base = before
      } else {
        // What traded before this tick is not known; count from here and let
        // history supply the rest.
        base = read.count
      }
      this.dayBase = base
      return Math.max(0, read.count - base)
    }
    if (isCount(tick.dayVolume)) {
      // A count that was not taken. An older reading from the same day: the
      // bar starts where the day's highest count stands. A quote's on a new
      // day: where this bar starts is not known yet.
      const sameDay = beforeDay !== null && zonedDayIndex(tick.time, this.zone) === beforeDay
      this.dayBase = wholeDay ? 0 : sameDay ? this.lastDay : null
      return 0
    }
    this.dayBase = wholeDay ? 0 : null
    return !tick.polled && isCount(tick.ltq) ? tick.ltq : 0
  }

  /** Volume of an intraday or day bar after folding a tick into it. */
  private foldVolume(cur: Bar, tick: LiveTick): number | undefined {
    const before = cur.volume ?? 0
    const read = this.takeCount(tick)
    if (read) {
      // A day bar's volume is the day's count: the feed keeps that total, and
      // history's figure is only a snapshot of it.
      if (this.isDayBar()) return Math.max(before, read.count)
      if (this.dayBase === null) {
        // A quote never sets where a bar's count starts.
        if (tick.polled) return cur.volume
        // The first count this bar has seen: it keeps the volume it has and
        // grows from here.
        this.dayBase = read.count - before
      }
      return Math.max(before, read.count - this.dayBase)
    }
    // An older count changes nothing, and a quote sums no last quantity.
    if (isCount(tick.dayVolume) || tick.polled) return cur.volume
    if (isCount(tick.ltq)) return before + tick.ltq
    return cur.volume
  }

  /** A number for the calendar period an instant falls in, rising with time. */
  private periodOf(time: number): number {
    if (this.bucketing.kind !== 'calendar') return time
    if (this.bucketing.unit === 'day') return zonedDayIndex(time, this.zone)
    if (this.bucketing.unit === 'week') return zonedWeekIndex(time, this.zone)
    const parts = utcSecondsToZonedParts(time, this.zone)
    return parts.year * 12 + (parts.month - 1)
  }

  /** Local midnight at the start of the period an instant falls in. */
  private periodStart(time: number): number {
    if (this.bucketing.kind !== 'calendar' || this.bucketing.unit === 'day')
      return startOfZonedDay(time, this.zone)
    if (this.bucketing.unit === 'week') return startOfZonedWeek(time, this.zone)
    return startOfZonedMonth(time, this.zone)
  }
}

/**
 * Put a closed bar a late trade changed or created where its time belongs:
 * replace the bar at that time, or insert it in order.
 */
export function insertLiveBar(bars: Bar[], bar: Bar): 'append' | 'replace' | 'insert' {
  let i = bars.length - 1
  while (i >= 0 && bars[i].time > bar.time) i--
  if (i >= 0 && bars[i].time === bar.time) {
    bars[i] = bar
    return 'replace'
  }
  if (i === bars.length - 1) {
    bars.push(bar)
    return 'append'
  }
  bars.splice(i + 1, 0, bar)
  return 'insert'
}

/** Take out the bar at a time: a flat bar that moved on (`LiveBarUpdate.replaces`). */
export function removeLiveBar(bars: Bar[], time: number): boolean {
  for (let i = bars.length - 1; i >= 0 && bars[i].time >= time; i--) {
    if (bars[i].time === time) {
      bars.splice(i, 1)
      return true
    }
  }
  return false
}

/**
 * Put a live bar into a time-ordered list: replace the bar at its time, or
 * append it after the last. A bar older than the last with no match is left
 * out rather than inserted out of order.
 */
export function upsertLiveBar(bars: Bar[], bar: Bar): 'append' | 'replace' | 'skip' {
  const n = bars.length
  if (n === 0 || bar.time > bars[n - 1].time) {
    bars.push(bar)
    return 'append'
  }
  for (let i = n - 1; i >= 0 && bars[i].time >= bar.time; i--) {
    if (bars[i].time === bar.time) {
      bars[i] = bar
      return 'replace'
    }
  }
  return 'skip'
}

/** What the clock needs from the history owner to repair the bar that just closed. */
export interface LiveHistory {
  bars(): readonly Bar[]
  refresh(): Promise<unknown>
}

export interface LiveBarClockOptions {
  /** The builder in use, read each time the clock fires: a host replaces it on every load. */
  builder(): LiveBarBuilder | null
  /** A bar the clock opened, for the host to draw. */
  onBar(update: LiveBarUpdate): void
  /**
   * The history owner. A bar the clock opens is not handed to it (a flat bar
   * nobody traded is not history), so the clock asks it for the bar that just
   * closed itself, as a tick opening the bucket would have.
   */
  history?(): LiveHistory | null
  /** UTC seconds. */
  now?(): number
  setTimer?(run: () => void, ms: number): unknown
  clearTimer?(handle: unknown): void
  /**
   * How long after a boundary the clock looks, in milliseconds. A tick that
   * lands first opens the bar itself; this only covers the timer firing a
   * hair before the wall clock agrees the boundary has passed. Kept small: a
   * candle opens on time, with nothing added to what the feed delivers.
   */
  graceMs?: number
  /** How long after a boundary history is asked for the bar that closed. */
  repairDelayMs?: number
}

/** Longest single wait, so a sleeping machine or a moved clock is caught up within this. */
const MAX_WAIT_MS = 30 * 60_000

/**
 * Opens a bar at each intraday boundary while the session is open, by asking
 * the builder at the right moments. One timer at a time, aimed at the next
 * boundary or the next session's open; nothing runs while the market is shut
 * beyond one wake-up every half hour.
 */
export class LiveBarClock {
  private readonly opts: LiveBarClockOptions
  private timer: unknown = null
  private repair: unknown = null
  private running = false

  constructor(options: LiveBarClockOptions) {
    this.opts = options
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now() / 1000
  }

  private set(run: () => void, ms: number): unknown {
    return this.opts.setTimer ? this.opts.setTimer(run, ms) : setTimeout(run, ms)
  }

  private clear(handle: unknown): void {
    if (handle === null) return
    if (this.opts.clearTimer) this.opts.clearTimer(handle)
    else clearTimeout(handle as ReturnType<typeof setTimeout>)
  }

  /** Start, or aim again after the builder or its hours changed. */
  start(): void {
    this.running = true
    this.schedule()
  }

  stop(): void {
    this.running = false
    this.clear(this.timer)
    this.timer = null
    this.clear(this.repair)
    this.repair = null
  }

  private schedule(): void {
    this.clear(this.timer)
    this.timer = null
    if (!this.running) return
    const builder = this.opts.builder()
    if (!builder) return
    const now = this.now()
    const at = builder.nextClockAt(now)
    if (at === null) return
    const grace = this.opts.graceMs ?? 100
    const wait = Math.min(MAX_WAIT_MS, Math.max(0, (at - now) * 1000) + grace)
    this.timer = this.set(() => {
      this.timer = null
      this.fire()
    }, wait)
  }

  private fire(): void {
    const builder = this.opts.builder()
    if (this.running && builder) {
      const update = builder.onClock(this.now())
      if (update) {
        this.opts.onBar(update)
        // A flat bar that moved on closed nothing anybody traded: there is
        // nothing for history to repair.
        if (update.replaces === undefined) this.repairAfter(update.bar.time)
      }
    }
    this.schedule()
  }

  /**
   * Ask history for the bar that just closed, unless a tick has already
   * opened this bucket in the history owner, which then repairs on its own.
   */
  private repairAfter(bucket: number): void {
    const history = this.opts.history?.()
    if (!history) return
    this.clear(this.repair)
    this.repair = this.set(() => {
      this.repair = null
      const held = history.bars()
      const newest = held[held.length - 1]
      if (!newest || newest.time < bucket) void history.refresh().catch(() => {})
    }, this.opts.repairDelayMs ?? 2500)
  }
}

/**
 * How often history is read again for the forming bar while ticks carry no
 * traded volume. A depth update without one leaves history as the only source
 * of the bar's volume, and a volume pane frozen for a whole bar reads as a
 * dead feed.
 */
export const RESYNC_WITHOUT_TICK_VOLUME_MS = 30_000
/**
 * The same once ticks carry the day's volume: the stream moves the bar, and
 * this is only the periodic re-sync behind it.
 */
export const RESYNC_MS = 120_000

export interface HistoryResyncOptions {
  /** The builder in use, read each time: whether its ticks carry volume sets the pace. */
  builder(): LiveBarBuilder | null
  /** Ask history for the tail again. */
  refresh(): void
  /** Whether a re-read is worth making now: a hidden page skips it. Defaults to always. */
  active?(): boolean
  setTimer?(run: () => void, ms: number): unknown
  clearTimer?(handle: unknown): void
}

/**
 * The periodic history re-sync behind a live chart: every 30 seconds while
 * ticks carry no traded volume, every two minutes once they do. Market data
 * is live from the stream; REST is the first snapshot and this re-sync.
 */
export class HistoryResync {
  private readonly opts: HistoryResyncOptions
  private timer: unknown = null
  private running = false

  constructor(options: HistoryResyncOptions) {
    this.opts = options
  }

  start(): void {
    this.running = true
    this.schedule()
  }

  stop(): void {
    this.running = false
    if (this.timer === null) return
    if (this.opts.clearTimer) this.opts.clearTimer(this.timer)
    else clearTimeout(this.timer as ReturnType<typeof setTimeout>)
    this.timer = null
  }

  private schedule(): void {
    if (!this.running) return
    const wait = this.opts.builder()?.hasTickVolume() ? RESYNC_MS : RESYNC_WITHOUT_TICK_VOLUME_MS
    const run = () => {
      this.timer = null
      if (!this.running) return
      if (this.opts.active?.() ?? true) this.opts.refresh()
      this.schedule()
    }
    this.timer = this.opts.setTimer ? this.opts.setTimer(run, wait) : setTimeout(run, wait)
  }
}
