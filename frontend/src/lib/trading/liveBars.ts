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
 *   next bar opens at the last close with no volume, even when no tick has
 *   come, but only while the exchange's session is open. Such a bar has seen no
 *   trade, so the first tick into it becomes its open, high, low and close
 *   rather than being measured against a price nobody traded at.
 * - **Day, week and month bars are live.** Today's, this week's and this
 *   month's bar take every tick, bucketed by the exchange's own calendar in
 *   its own zone rather than by a fixed count of seconds.
 * - **Volume comes from the tick when the tick carries it.** With the day's
 *   cumulative volume on a tick, the forming bar's volume is the count now less
 *   the count when the bar began. Without it a last traded quantity is summed,
 *   and without either the bar keeps what history said until the next re-sync.
 * - **No bar opens outside a session.** A tick that would start a new bar while
 *   the calendar says the exchange is shut, such as the snapshot a feed sends
 *   on subscribing at night or a pre-open price, moves no bar.
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
  /** Opened by the clock, with no trade yet: a flat bar at the last close. */
  placeholder: boolean
}

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
  /** The clock opened the current bar and no trade has reached it yet. */
  private empty = false
  /** The current bar has taken a tick or was opened by the clock: it is ahead of history. */
  private live = false
  /** A tick has been folded since the last seed, so the next bucket's first tick is its open. */
  private streamed = false
  private provisional = false
  /** The day's count when the current bar began; null while not known. */
  private dayBase: number | null = null
  /** The last day count a tick carried. */
  private lastDay: number | null = null
  /** Whether `lastDay` is where the next bar's count starts: false after a gap in the stream. */
  private lastDayTrusted = false
  /** Seconds after local midnight at which history stamps day, week and month bars. */
  private stampOffset = 0
  /** Tick time of the last tick that carried a day count. */
  private dayCountAt: number | null = null
  /**
   * The last bar handed to the host. A history owner that has not heard from
   * history yet hands exactly this back, and that is not news.
   */
  private echo: Bar | null = null

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
   * nothing was watching the bucket in between.
   */
  seed(bar: Bar): void {
    this.cur = { ...bar }
    this.echo = null
    this.empty = false
    this.live = false
    this.streamed = false
    this.provisional = false
    this.dayBase = null
    this.lastDayTrusted = false
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

  /** Whether any tick so far carried the day's cumulative volume. */
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
   * Fold one tick. Returns the bar it changed or opened, or null when the
   * tick moves no bar: an unusable price, or a bar that would open while the
   * exchange is shut.
   */
  onTick(tick: LiveTick): LiveBarUpdate | null {
    const price = tick.price
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(tick.time)) return null
    const bucket = this.bucketStart(tick.time)
    const cur = this.cur
    // The bar forming, or a late tick from an earlier bucket, which is folded
    // into the current bar rather than reopening a closed one.
    if (cur && bucket <= cur.time) {
      this.fold(cur, tick)
      this.streamed = true
      this.live = true
      this.echo = { ...cur }
      return { bar: { ...cur }, isNew: false, provisional: this.provisional, placeholder: false }
    }
    if (this.session && !this.session.sessionAt(tick.time)) return null
    const provisional = cur === null || !this.streamed
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
    this.empty = false
    this.live = true
    this.streamed = true
    this.provisional = provisional
    this.echo = { ...bar }
    return { bar: { ...bar }, isNew: true, provisional, placeholder: false }
  }

  /**
   * Open the next intraday bar at a boundary when no tick has. Flat at the
   * last close, no volume. Only inside a session, and only for the bucket
   * straight after the one held or the session's first: a later bucket means
   * the clock slept through some, and a price for those is not known.
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
    if (bucket !== cur.time + seconds && !opensSession) return null
    const close = cur.close
    const bar: Bar = { time: bucket, open: close, high: close, low: close, close, volume: 0 }
    const provisional = !this.streamed
    this.cur = bar
    this.empty = true
    this.live = true
    this.provisional = provisional
    this.dayBase = opensSession
      ? 0
      : this.lastDayTrusted && this.lastDay !== null
        ? this.lastDay
        : null
    this.echo = { ...bar }
    return { bar: { ...bar }, isNew: true, provisional, placeholder: true }
  }

  /**
   * Take history's bar for the bucket now forming. A provisional bar takes
   * history's open, since its own is only the first tick it saw. Both sides
   * observed real prices, so the extremes are the union, and the larger
   * volume wins, because volume inside a bar only grows. The close stays with
   * the ticks: history is a snapshot taken before its request went out, and a
   * price that steps back on every repair is the one thing a live chart must
   * not do. A bar the clock opened has seen no trade, so history's replaces it
   * whole.
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
   * One history snapshot with the forming bar merged in, one bar per bucket.
   *
   * - The bar history holds for the forming bucket is reconciled with the
   *   builder's. For day, week and month bars that is matched by period, and
   *   where history stamped the period differently from the builder, history's
   *   stamp is kept and the builder moves to it: two bars for one week is the
   *   duplicate this exists to prevent.
   * - A forming bar history does not have yet is put after its last bar.
   * - When the builder has seen nothing since history last set it, or history
   *   is already a bucket ahead, the builder follows history instead.
   */
  mergeSnapshot(bars: readonly Bar[]): Bar[] {
    const out = bars.slice()
    const cur = this.cur
    if (!cur || !out.length) return out
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
    cur.close = price
    const volume = this.foldVolume(cur, tick)
    if (volume === undefined) delete cur.volume
    else cur.volume = volume
    // A level, not a flow: a tick without one leaves the bar without one,
    // which is honest where a carried-over figure would not be.
    if (isCount(tick.oi)) cur.oi = tick.oi
    else delete cur.oi
  }

  /** Whether an intraday bucket holds its session's open. */
  private opensSession(bucket: number): boolean {
    if (this.bucketing.kind !== 'intraday' || !this.session) return false
    const session = this.session.sessionAt(bucket)
    return (
      session !== null && session.open >= bucket && session.open < bucket + this.bucketing.seconds
    )
  }

  /** Volume for a bar a tick opens. */
  private openingVolume(bucket: number, tick: LiveTick): number {
    const day = tick.dayVolume
    if (isCount(day)) {
      let base: number
      if (this.bucketing.kind === 'calendar' || this.opensSession(bucket)) {
        // Everything traded today so far belongs to this bar.
        base = 0
      } else if (this.lastDayTrusted && this.lastDay !== null && day >= this.lastDay) {
        base = this.lastDay
      } else {
        // What traded before this tick is not known; count from here and let
        // history supply the rest.
        base = day
      }
      this.dayBase = base
      this.noteDay(day, tick.time)
      return Math.max(0, day - base)
    }
    this.dayBase = null
    return isCount(tick.ltq) ? tick.ltq : 0
  }

  /** Volume after folding a tick into the current bar. */
  private foldVolume(cur: Bar, tick: LiveTick): number | undefined {
    const day = tick.dayVolume
    const before = cur.volume ?? 0
    if (isCount(day)) {
      if (this.dayBase === null || (this.lastDay !== null && day < this.lastDay)) {
        // The first count this bar has seen, or a count that went backwards
        // (a new day under a week's bar, or a feed that reports a rolling
        // window): the bar keeps the volume it has and grows from here. A day
        // that restarted loses only what traded before its first reading,
        // which the next re-sync brings back.
        this.dayBase = day - before
      }
      this.noteDay(day, tick.time)
      return Math.max(before, day - this.dayBase)
    }
    if (isCount(tick.ltq)) return before + tick.ltq
    return cur.volume
  }

  private noteDay(day: number, time: number): void {
    this.lastDay = day
    this.lastDayTrusted = true
    this.dayCountAt = time
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
        this.repairAfter(update.bar.time)
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
