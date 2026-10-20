/**
 * The phone chart's host for the chart engine: one instrument, one interval,
 * candles over a volume pane, live tick by tick.
 *
 * Built on the same pieces as every /trading pane, so the two never disagree
 * about a candle: the forming bar comes from `LiveBarBuilder` and opens on the
 * clock through `LiveBarClock`, history merges through the builder, the
 * re-sync is `HistoryResync`, the feed's state is `LiveStatusTracker` and the
 * day's volume is read off each frame by `FrameExtrasTap`
 * (`lib/trading/live*.ts`). What this file adds is the phone's own small host:
 * no drawing tools, no order lines, no panels.
 *
 * Framework-agnostic, like the terminal: the page drives it through plain
 * methods and hears back through callbacks, and the tick path never goes
 * through React.
 */
import {
  type Bar,
  type Chart,
  createChart,
  DataLoadingController,
  type DataLoadingSnapshot,
  InvalidationLevel,
  type LtpEvent,
  type MarketDepth,
  OpenAlgoDataFeed,
  OpenAlgoWsFeed,
  type SeriesApi,
  withBarCache,
} from 'openalgo-charts'
import { chartMotionOptions } from '@/lib/trading/chartMotion'
import {
  CHART_READY,
  type ChartStateView,
  chartLoadFailed,
  chartNoData,
} from '@/lib/trading/chartState'
import { buildChartTheme, quietVolumeStyle } from '@/lib/trading/chartTheme'
import { lookbackDays } from '@/lib/trading/intervals'
import {
  HistoryResync,
  insertLiveBar,
  LiveBarBuilder,
  LiveBarClock,
  type LiveBarUpdate,
  type LiveCountdown,
  removeLiveBar,
  upsertLiveBar,
} from '@/lib/trading/liveBars'
import { type LiveStatus, LiveStatusTracker } from '@/lib/trading/liveStatus'
import { FrameExtrasTap, WAKE_RECONNECT_MS } from '@/lib/trading/liveTicks'
import { needsPreviousClose, previousClose } from '@/lib/trading/previousClose'
import { applySessionHours, sessionCalendarFor } from '@/lib/trading/sessionHours'
import { volumePoint } from '@/lib/trading/volumeSettings'
import type { AppMode } from '@/stores/themeStore'
import { CHART_BRANDING } from '@/sudarshan/brand'

/** How often the quote fallback asks while the stream is down: the same as /trading's. */
export const QUOTE_FALLBACK_MS = 4000

/** Away longer than this, and the chart tests its stream on coming back. */
export { WAKE_RECONNECT_MS }

/** Bars in view when a chart opens: enough candles to read a trend at phone width. */
export const PHONE_VISIBLE_BARS = 70

/** An index is quoted, never traded: no book, no volume, LTP mode. */
export function isQuoteOnly(exchange: string): boolean {
  return exchange.endsWith('_INDEX')
}

/** The broker's interval code for one of the chart's chips. */
export function apiInterval(chip: string): string {
  return chip === '1D' ? 'D' : chip
}

/** One price as the header shows it. */
export interface PhoneTick {
  ltp: number
  /**
   * The exchange's time for it, UTC seconds, when the feed sent one: the last
   * trade's time where the frame carried it, else the frame's own.
   */
  time: number | null
  /** The exchange's time of the last trade, UTC seconds, when the frame carried it. */
  tradeTime: number | null
  /** Wall clock, milliseconds, when it arrived. */
  at: number
  /** A quote request standing in for the stream, not a tick from it. */
  polled: boolean
}

export interface PhoneChartCallbacks {
  /** Every price, as it arrives. */
  onTick(tick: PhoneTick): void
  /** The previous session's close, when a frame or the first quote carries it. */
  onPrevClose?(value: number): void
  /** Whether the prices are real-time, delayed, stale or from a closed market. */
  onStatus(status: LiveStatus): void
  /** Loading, drawn, no bars, or failed: the same states and words as a /trading pane. */
  onLoad(state: ChartStateView): void
  /**
   * The session's close, in UTC seconds, while the forming bar would run past
   * it (an hourly bar from 15:15 ends with the session at 15:30, not at
   * 16:15), so the page can say when it closes instead of a countdown that
   * overshoots; null otherwise.
   */
  onSessionClose?(at: number | null): void
}

export interface PhoneChartOptions {
  container: HTMLElement
  apiKey: string
  wsUrl: string
  appMode: AppMode
  callbacks: PhoneChartCallbacks
}

export class PhoneChart {
  private readonly apiKey: string
  private readonly cb: PhoneChartCallbacks
  private readonly container: HTMLElement
  private appMode: AppMode
  private chart: Chart
  private price: SeriesApi
  private volume: SeriesApi | null = null
  private readonly data: DataLoadingController
  private readonly ws: OpenAlgoWsFeed
  private readonly frames = new FrameExtrasTap()
  private readonly status: LiveStatusTracker
  private readonly clock: LiveBarClock
  private readonly resync: HistoryResync
  private builder: LiveBarBuilder | null = null
  private bars: Bar[] = []
  private sym: { symbol: string; exchange: string } | null = null
  private interval = '5m'
  private ticket = 0
  private subscribed: { mode: 'LTP' | 'Depth'; symbol: string; exchange: string } | null = null
  private fallback: ReturnType<typeof setInterval> | null = null
  private loadingOlder = false
  private wsOpened = false
  private hiddenAt: number | null = null
  /** The feed's state as last reported, for the countdown. */
  private lastStatus: LiveStatus | null = null
  /** Whether the countdown to the bar's close is shown, and the timer that moves it. */
  private countdownOn = false
  private countdownTimer: ReturnType<typeof setInterval> | null = null
  /** The session close last reported to the page in place of a countdown. */
  private closesAt: number | null = null
  /** A wake check is waiting for the stream to answer. */
  private checking = false
  private loadState: ChartStateView = CHART_READY
  /** A price has come off the stream for this instrument since it loaded. */
  private streamed = false
  private destroyed = false
  private readonly offs: Array<() => void> = []

  constructor(opts: PhoneChartOptions) {
    this.apiKey = opts.apiKey
    this.cb = opts.callbacks
    this.container = opts.container
    this.appMode = opts.appMode
    ;({ chart: this.chart, price: this.price, volume: this.volume } = this.buildChart(''))

    const rest = new OpenAlgoDataFeed({ baseUrl: '', apiKey: this.apiKey })
    this.data = new DataLoadingController(withBarCache(rest, { ttlMs: 10 * 60_000 }), {
      // The server's clock, not the phone's: a bar closes when the exchange's minute turns.
      now: () => this.frames.clock.nowSec(),
      // The re-sync below runs instead of the controller's poll.
      pollIntervalMs: 0,
      refreshOnBarClose: true,
      refreshOnGap: true,
      refreshWindowBars: 5,
      pageSize: 500,
      maxEmptyPages: 4,
      maxBars: 20_000,
    })
    this.offs.push(this.data.subscribe((snapshot) => this.applySnapshot(snapshot)))

    this.status = new LiveStatusTracker({
      exchange: '',
      onChange: (status) => {
        if (this.destroyed) return
        this.lastStatus = status
        this.syncCountdown()
        this.cb.onStatus(status)
      },
    })
    this.clock = new LiveBarClock({
      builder: () => (this.destroyed ? null : this.builder),
      onBar: (update) => this.applyLiveBar(update),
      history: () => this.data,
      now: () => this.frames.clock.seconds(),
    })
    this.resync = new HistoryResync({
      builder: () => this.builder,
      refresh: () => {
        if (this.sym) void this.data.refresh()
      },
      active: () => document.visibilityState !== 'hidden',
    })
    this.resync.start()
    // The server's clock moved: the next candle's open is aimed again by it.
    this.offs.push(
      this.frames.clock.onChange(() => {
        if (!this.destroyed && this.builder?.hasSession()) this.clock.start()
      })
    )

    this.ws = new OpenAlgoWsFeed({
      url: opts.wsUrl,
      apiKey: this.apiKey,
      socketFactory: this.frames.socketFactory,
    })
    this.offs.push(this.ws.onState((state) => this.onWsState(state)))
    this.offs.push(this.ws.onLtp((event) => this.onLtpEvent(event)))
    this.offs.push(
      this.ws.onDepth((symbol, exchange, depth) => this.onDepthEvent(symbol, exchange, depth))
    )
    document.addEventListener('visibilitychange', this.onVisibility)
    this.ws.connect()
  }

  /**
   * Show an instrument at an interval. Resolves once history is drawn, or has
   * failed. Either way the price stays real-time: a chart whose history did
   * not load still streams, and Try again reloads only the history. Another
   * timeframe of the same instrument keeps its subscription, its feed state
   * and its price: only the candles load again.
   */
  async load(symbol: string, exchange: string, chip: string): Promise<void> {
    if (this.destroyed) return
    const ticket = ++this.ticket
    const same = this.sym?.symbol === symbol && this.sym.exchange === exchange
    this.stopLive()
    if (!same) {
      this.unsubscribe()
      this.streamed = false
    }
    this.sym = { symbol, exchange }
    this.interval = apiInterval(chip)
    this.bars = []
    this.setLoad({ kind: 'loading', symbol, interval: chip })
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price, volume: this.volume } = this.buildChart(exchange))
    this.chart.setDataContext({ symbol, exchange, interval: this.interval })
    applySessionHours(this.chart, exchange, this.apiKey)
    const to = this.frames.clock.nowSec()
    const request = {
      symbol,
      exchange,
      interval: this.interval,
      from: to - lookbackDays(this.interval) * 86400,
      to,
    }
    let bars: readonly Bar[] = []
    let failure: unknown = null
    try {
      bars = await this.data.load(request)
    } catch (error) {
      failure = error
    }
    if (ticket !== this.ticket || this.destroyed) return
    failure ??= bars.length ? null : this.data.getState().error
    if (failure) this.setLoad(chartLoadFailed(symbol, chip, cleanError(failure)))
    else if (!bars.length) this.setLoad(chartNoData(symbol, chip))
    else {
      this.setLoad(CHART_READY)
      // History's last close stands in until the first tick, never over one.
      const last = bars[bars.length - 1]
      if (!this.streamed)
        this.cb.onTick({
          ltp: last.close,
          time: null,
          tradeTime: null,
          at: Date.now(),
          polled: true,
        })
    }
    this.startLive(exchange, same, to)
    if (!same || !this.subscribed) this.subscribe(symbol, exchange)
    this.syncCountdown()
    if (!same) void this.firstQuote(symbol, exchange, ticket)
  }

  /**
   * Analyze mode came on or went off: redraw in its colours, keeping the bars
   * and the view. The chart is built anew for it, as /trading does.
   */
  setAppMode(mode: AppMode): void {
    if (mode === this.appMode || this.destroyed) return
    this.appMode = mode
    const sym = this.sym
    if (!sym) return
    const range = this.bars.length ? this.chart.getVisibleLogicalRange() : null
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price, volume: this.volume } = this.buildChart(sym.exchange))
    this.chart.setDataContext({
      symbol: sym.symbol,
      exchange: sym.exchange,
      interval: this.interval,
    })
    applySessionHours(this.chart, sym.exchange, this.apiKey)
    if (!this.bars.length) return
    this.draw()
    if (range) this.chart.setVisibleLogicalRange(range)
    this.syncCountdown()
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    this.stopCountdownTimer()
    document.removeEventListener('visibilitychange', this.onVisibility)
    for (const off of this.offs.splice(0)) off()
    this.clock.stop()
    this.resync.stop()
    this.status.destroy()
    this.stopFallback()
    try {
      this.ws.close()
    } catch {
      /* already closed */
    }
    this.data.destroy()
    this.chart.destroy()
  }

  /** The chart the page's long-press reads from: bar under an x, price under a y. */
  barAt(x: number): Bar | null {
    if (!this.bars.length) return null
    const index = Math.round(this.chart.timeScale.xToIndex(x))
    if (index < 0 || index >= this.bars.length) return null
    return this.bars[index]
  }

  /** The x, in the chart's own pixels, of the bar a long-press is on. */
  barX(bar: Bar): number | null {
    const index = this.bars.findIndex((b) => b.time === bar.time)
    return index < 0 ? null : this.chart.timeScale.indexToX(index)
  }

  private readonly onVisibility = () => {
    const visible = document.visibilityState !== 'hidden'
    this.data.setVisible(visible)
    if (!visible) {
      this.hiddenAt ??= Date.now()
      return
    }
    const hiddenAt = this.hiddenAt
    this.hiddenAt = null
    if (hiddenAt !== null && Date.now() - hiddenAt > WAKE_RECONNECT_MS)
      void this.checkStream(hiddenAt)
  }

  /**
   * Back after the phone slept. A socket can look open and be dead, so it is
   * tested rather than trusted or thrown away: a frame since the page was
   * hidden, or any answer to a ping within a second and a half, keeps it;
   * silence replaces it. "Reconnecting" shows while the answer is awaited, so
   * old prices are never shown as current.
   */
  private async checkStream(hiddenAt: number): Promise<void> {
    if (this.destroyed || this.checking || !this.wsOpened) return
    const heard = this.frames.lastFrameAt
    if (heard !== null && heard > hiddenAt) return
    this.checking = true
    this.status.setTransport('connecting')
    try {
      const alive = await this.frames.probe()
      if (this.destroyed) return
      if (!alive) this.reconnect()
      else if (this.ws.isReady()) this.status.setTransport('live')
    } finally {
      this.checking = false
    }
  }

  /** A fresh stream after the phone wakes: "Reconnecting" at once, real-time again within a second or two. */
  private reconnect(): void {
    if (this.destroyed) return
    this.status.setTransport('connecting')
    try {
      this.ws.close()
    } catch {
      /* already closed */
    }
    this.ws.connect()
    // Closing forgets the subscriptions; the new connection replays these.
    const sub = this.subscribed
    if (sub) {
      this.subscribed = null
      this.subscribe(sub.symbol, sub.exchange)
    }
  }

  /**
   * The countdown to the forming bar's close, beside the last price: shown
   * for an intraday timeframe while the session is open, so a trader can
   * watch the next candle arrive. Not for a day bar, whose engine countdown
   * runs to midnight rather than the close, and not in a closed market, where
   * no bar is coming. Where the session closes before the bar would (the last
   * hourly bar), the page is told the close instead, and says it. The engine
   * paints the countdown only when something else repaints, so while it is up
   * one timer repaints the price pane each second, and asks again whether it
   * should be up; neither reads any data.
   */
  private syncCountdown(): void {
    const status = this.lastStatus
    const countdown: LiveCountdown =
      this.destroyed || !this.sym || !this.builder || !status || status.line.kind === 'closed'
        ? { kind: 'off' }
        : this.builder.countdown(this.frames.clock.seconds())
    const on = countdown.kind === 'bar'
    if (on !== this.countdownOn && !this.destroyed) {
      this.countdownOn = on
      this.chart.setAxisChromeOptions({ barCountdown: on })
    }
    const closesAt = countdown.kind === 'closes' ? countdown.at : null
    if (closesAt !== this.closesAt) {
      this.closesAt = closesAt
      if (!this.destroyed) this.cb.onSessionClose?.(closesAt)
    }
    if (on && !this.countdownTimer)
      this.countdownTimer = setInterval(() => {
        if (document.visibilityState === 'hidden' || this.destroyed) return
        this.chart.invalidate((mask) =>
          mask.invalidatePane(0, { level: InvalidationLevel.Light, autoScale: false })
        )
        this.syncCountdown()
      }, 1000)
    if (!on) this.stopCountdownTimer()
  }

  private stopCountdownTimer(): void {
    if (!this.countdownTimer) return
    clearInterval(this.countdownTimer)
    this.countdownTimer = null
  }

  /**
   * A fresh chart for an instrument: candles, and under them a volume pane
   * for anything that trades. An index has no volume, so its chart has no
   * volume pane at all. Built anew per load, as /trading does, so nothing of
   * the last instrument's scales or panes carries over.
   */
  private buildChart(exchange: string): {
    chart: Chart
    price: SeriesApi
    volume: SeriesApi | null
  } {
    this.container.innerHTML = ''
    this.countdownOn = false
    const chart = createChart(this.container, {
      theme: buildChartTheme('light', this.appMode),
      priceAxisWidth: 64,
      navigation: { defaultVisibleBars: PHONE_VISIBLE_BARS },
      // The countdown to the bar's close comes and goes with the session
      // (see syncCountdown), and counts by the server's clock.
      axisChrome: { barCountdown: false, clock: () => this.frames.clock.seconds() },
      shortcuts: false,
      ...chartMotionOptions(),
      branding: CHART_BRANDING,
    })
    const price = chart.addSeries('candlestick')
    let volume: SeriesApi | null = null
    if (exchange && !isQuoteOnly(exchange)) {
      volume = chart.addSeries('histogram', {
        paneIndex: 1,
        priceFormat: { type: 'volume' },
        style: { priceLineVisible: false, lastValueVisible: false },
      })
      chart.setPaneWeight(0, 4)
      chart.setPaneWeight(1, 1)
    }
    chart.setHistoryLoader(() => void this.loadOlder())
    return { chart, price, volume }
  }

  private volumeStyle() {
    const theme = this.chart.theme()
    return quietVolumeStyle(
      { upColor: theme.upColor, downColor: theme.downColor },
      theme.background
    )
  }

  private draw(): void {
    this.price.setData(this.bars)
    if (this.volume) {
      const style = this.volumeStyle()
      this.volume.setData(
        this.bars.map((bar, i) =>
          volumePoint(bar, this.bars[i - 1]?.close, bar.volume ?? 0, style, true)
        )
      )
    }
  }

  /** Draw one bar again where it already stands, the tail or just before it. */
  private drawBar(bar: Bar): void {
    let i = this.bars.length - 1
    while (i >= 0 && this.bars[i].time > bar.time) i--
    if (i < 0 || this.bars[i].time !== bar.time) return
    this.price.update(bar)
    this.volume?.update(
      volumePoint(bar, this.bars[i - 1]?.close, bar.volume ?? 0, this.volumeStyle(), true)
    )
  }

  private applySnapshot(snapshot: DataLoadingSnapshot): void {
    const request = snapshot.request
    const sym = this.sym
    if (
      this.destroyed ||
      !request ||
      !sym ||
      request.symbol !== sym.symbol ||
      request.exchange !== sym.exchange ||
      request.interval !== this.interval
    )
      return
    if (snapshot.reason === 'live' || snapshot.reason === 'state') return
    if (!snapshot.bars.length) return
    let next = [...snapshot.bars]
    if (this.builder) next = this.builder.mergeSnapshot(next, this.frames.clock.nowSec())
    const before = snapshot.reason === 'prepend' ? this.chart.getVisibleLogicalRange() : null
    const inserted = next.length - this.bars.length
    this.bars = next
    this.draw()
    if (snapshot.reason === 'prepend' && before && inserted > 0)
      this.chart.setVisibleLogicalRange({ from: before.from + inserted, to: before.to + inserted })
  }

  private async loadOlder(): Promise<void> {
    if (this.loadingOlder || this.destroyed || !this.sym) {
      this.chart.historyLoadComplete()
      return
    }
    this.loadingOlder = true
    try {
      await this.data.loadMore()
    } catch {
      /* the next pan asks again */
    } finally {
      this.loadingOlder = false
      if (!this.destroyed) this.chart.historyLoadComplete()
    }
  }

  /**
   * The forming bar for what was just loaded, seeded from history's last bar
   * (read at `readAt`), and the exchange's hours once they arrive. A new
   * instrument starts its feed state over; another timeframe of the same one
   * keeps it.
   */
  private startLive(exchange: string, keepStatus: boolean, readAt: number): void {
    const last = this.bars[this.bars.length - 1] ?? this.data.bars().at(-1)
    const builder = LiveBarBuilder.supports(this.interval)
      ? new LiveBarBuilder({ interval: this.interval, anchorSec: last?.time })
      : null
    if (builder && last) builder.seed(last, readAt)
    this.builder = builder
    if (!keepStatus) {
      this.status.setCalendar(null)
      this.status.reset(exchange)
    }
    const ticket = this.ticket
    void sessionCalendarFor(exchange, this.apiKey).then((calendar) => {
      if (this.destroyed || ticket !== this.ticket || !calendar) return
      this.status.setCalendar(calendar)
      if (!builder || this.builder !== builder) return
      builder.setSession(calendar)
      this.clock.start()
      this.syncCountdown()
    })
  }

  private stopLive(): void {
    this.clock.stop()
    this.builder = null
  }

  private subscribe(symbol: string, exchange: string): void {
    const mode = isQuoteOnly(exchange) ? 'LTP' : 'Depth'
    this.subscribed = { mode, symbol, exchange }
    if (mode === 'LTP') this.ws.subscribe('LTP', symbol, exchange)
    else this.ws.subscribe('Depth', symbol, exchange, 5)
  }

  private unsubscribe(): void {
    const sub = this.subscribed
    this.subscribed = null
    if (!sub) return
    try {
      this.ws.unsubscribe(sub.mode, sub.symbol, sub.exchange)
    } catch {
      /* not subscribed */
    }
  }

  /**
   * The first quote: a price before the first tick, and the previous session's
   * close for the change. Where the quote's own previous close cannot be
   * trusted, daily history answers instead, exactly as the watchlist does.
   */
  private async firstQuote(symbol: string, exchange: string, ticket: number): Promise<void> {
    const quote = await this.quote(symbol, exchange)
    if (ticket !== this.ticket || this.destroyed) return
    const ltp = typeof quote?.ltp === 'number' && quote.ltp > 0 ? quote.ltp : null
    if (ltp !== null && !this.streamed)
      this.cb.onTick({ ltp, time: null, tradeTime: null, at: Date.now(), polled: true })
    if (quote && ltp !== null && !needsPreviousClose(quote.prev_close, ltp)) {
      this.cb.onPrevClose?.(quote.prev_close as number)
      return
    }
    const calendar = await sessionCalendarFor(exchange, this.apiKey)
    const open = calendar?.sessionAt(this.frames.clock.nowSec()) != null
    const value = await previousClose(this.apiKey, symbol, exchange, open)
    if (value !== null && ticket === this.ticket && !this.destroyed) this.cb.onPrevClose?.(value)
  }

  private async quote(
    symbol: string,
    exchange: string
  ): Promise<{ ltp?: number; prev_close?: number; volume?: number; oi?: number } | null> {
    try {
      const res = await fetch('/api/v1/quotes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apikey: this.apiKey, symbol, exchange }),
      })
      const body = (await res.json()) as { status?: string; data?: Record<string, number> }
      return res.ok && body.status === 'success' && body.data ? body.data : null
    } catch {
      return null
    }
  }

  private onWsState(state: string): void {
    if (this.destroyed) return
    if (state === 'closed' || state === 'error' || state === 'reconnecting') {
      this.status.setTransport('connecting')
      this.startFallback()
    }
    if (state === 'open') {
      // The stream is back: the quotes standing in for it stop now, not at
      // its first tick, which on a quiet instrument may be a while coming.
      this.stopFallback()
      this.status.setTransport('live')
      // The server's time, for the clock that opens candles.
      this.frames.ping()
      // Back after a break: what traded meanwhile was never built from ticks,
      // and what the last connection read about each instrument is old.
      this.builder?.resync()
      if (this.wsOpened) {
        this.frames.clear()
        if (this.sym) void this.data.refresh()
      }
      this.wsOpened = true
    }
  }

  private onLtpEvent(event: LtpEvent): void {
    const sym = this.sym
    if (!sym || event.symbol !== sym.symbol) return
    const extra = this.frames.get(event.symbol, event.exchange)
    this.streamTick({
      ltp: event.ltp,
      time: event.timeSec || null,
      tradeTime: extra?.tradeTime ?? null,
      dayVolume: event.volume ?? extra?.dayVolume,
      ltq: event.ltq,
      oi: extra?.oi,
      prevClose: extra?.prevClose,
    })
  }

  private onDepthEvent(symbol: string, exchange: string, depth: MarketDepth): void {
    const sym = this.sym
    if (!sym || symbol !== sym.symbol || !(depth.ltp > 0)) return
    // The day's volume and open interest come off the same frame through the
    // tap; a book update repeats the last traded quantity, so it is not summed.
    const extra = this.frames.get(symbol, exchange)
    this.streamTick({
      ltp: depth.ltp,
      time: depth.timeSec ?? null,
      tradeTime: extra?.tradeTime ?? null,
      dayVolume: extra?.dayVolume,
      oi: extra?.oi,
      prevClose: extra?.prevClose,
    })
  }

  private setLoad(state: ChartStateView): void {
    this.loadState = state
    this.cb.onLoad(state)
  }

  private streamTick(tick: {
    ltp: number
    time: number | null
    tradeTime: number | null
    dayVolume?: number
    ltq?: number
    oi?: number
    prevClose?: number
  }): void {
    this.status.setTransport('live')
    this.streamed = true
    // A previous close equal to the price is the session's own close, as some
    // feeds carry it before the open and after the close: not yesterday's.
    if (tick.prevClose && !needsPreviousClose(tick.prevClose, tick.ltp))
      this.cb.onPrevClose?.(tick.prevClose)
    this.onPrice({ ...tick, polled: false })
  }

  private onPrice(tick: {
    ltp: number
    time: number | null
    tradeTime?: number | null
    dayVolume?: number
    ltq?: number
    oi?: number
    polled: boolean
  }): void {
    const at = Date.now()
    this.status.noteTick(tick.time ?? undefined, !tick.polled, tick.tradeTime)
    this.cb.onTick({
      ltp: tick.ltp,
      time: tick.time,
      tradeTime: tick.tradeTime ?? null,
      at,
      polled: tick.polled,
    })
    const update = this.builder?.onTick({
      time: tick.time ?? this.frames.clock.nowSec(),
      price: tick.ltp,
      ltq: tick.ltq,
      dayVolume: tick.dayVolume,
      oi: tick.oi,
      polled: tick.polled,
      tradeTime: tick.tradeTime,
    })
    if (update) this.applyLiveBar(update)
  }

  private applyLiveBar(update: LiveBarUpdate): void {
    let redraw = false
    // A flat bar nobody traded gives its place to this one.
    if (update.replaces !== undefined) redraw = removeLiveBar(this.bars, update.replaces)
    // A late trade changed the bar before the forming one, or gave an
    // interval that had none a bar of its own. History's buffer keeps its own
    // copy until the repair after the boundary brings the bar back.
    if (update.previous) {
      const placed = insertLiveBar(this.bars, update.previous)
      if (placed === 'insert') redraw = true
      else if (!redraw) this.drawBar(update.previous)
    }
    if (upsertLiveBar(this.bars, update.bar) === 'skip') {
      if (redraw) this.draw()
      return
    }
    // Nothing in history yet, and now a candle is forming: show it.
    if (this.loadState.kind === 'empty') this.setLoad(CHART_READY)
    // A bar the clock opened stays out of history: nobody traded it.
    if (!update.placeholder)
      this.data.pushBar(update.bar, update.provisional ? { provisional: true } : undefined)
    if (redraw) this.draw()
    else this.drawBar(update.bar)
    // A new bar may be the session's last, which the countdown must not overshoot.
    if (update.isNew) this.syncCountdown()
  }

  private startFallback(): void {
    if (this.fallback) return
    this.fallback = setInterval(() => void this.pollQuote(), QUOTE_FALLBACK_MS)
  }

  private stopFallback(): void {
    if (!this.fallback) return
    clearInterval(this.fallback)
    this.fallback = null
  }

  private async pollQuote(): Promise<void> {
    const sym = this.sym
    if (!sym || this.destroyed || !this.fallback || this.ws.isReady()) return
    const quote = await this.quote(sym.symbol, sym.exchange)
    // The stream may be back by the time the answer is: a quote never speaks over it.
    if (!quote || this.destroyed || sym !== this.sym || !this.fallback || this.ws.isReady()) return
    if (!(typeof quote.ltp === 'number' && quote.ltp > 0)) return
    this.status.setTransport('polling')
    this.onPrice({
      ltp: quote.ltp,
      time: this.frames.clock.nowSec(),
      dayVolume: isQuoteOnly(sym.exchange) ? undefined : quote.volume,
      polled: true,
    })
  }
}

/**
 * A failed request's text without the request plumbing; `traderReason` words
 * the rest. The engine's own "history request failed (500)" names nothing a
 * trader can act on, so it is dropped and the shared default stands in.
 */
function cleanError(error: unknown): string {
  return String((error as Error)?.message ?? error ?? '')
    .replace(/^openalgo-charts:\s*/i, '')
    .replace(/^(?:openalgo\s+)?history request failed\b(?:\s*\(\d+\))?:?\s*/i, '')
    .replace(/^\/api\/v1\/[\w/]+\s+failed\s+\(\d+\)(:\s*)?/i, '')
    .trim()
}
