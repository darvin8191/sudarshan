/**
 * The phone chart's host for a computed chart (M4.13, C11): the sum of two instruments as one
 * line, real-time. `loadComputed({ legs, op: 'sum' }, interval)` is its one way in.
 *
 * Built on the pieces the phone chart and every /trading pane use, so a candle is the same candle
 * everywhere: history through the engine's loading controller (here over `ComputedFeed`, which
 * reads each leg and folds them), the forming bar from `LiveBarBuilder`, opened on the clock by
 * `LiveBarClock` and repaired from history after each close.
 *
 * It opens no socket of its own. The screen that shows it already follows both legs on the pooled
 * connection, and hands each leg's price over with `tick`: a computed chart adds no instrument to
 * the feed. The screen also owns the feed's words ("Real-time", "Reconnecting"); it calls `resync`
 * when its connection comes back, and the chart reads the gap itself when the page returns.
 *
 * Drawn as a line of closes: history's bar for a sum knows its open and close only (see
 * `computed.ts`), so wicks would claim a range nobody measured. No volume, no order lines, no
 * studies: a computed chart is not tradable.
 */
import {
  type Bar,
  type Chart,
  createChart,
  DataLoadingController,
  type DataLoadingSnapshot,
  OpenAlgoDataFeed,
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
import { buildChartTheme } from '@/lib/trading/chartTheme'
import { instrumentSessionFor } from '@/lib/trading/instrumentSession'
import { lookbackDays } from '@/lib/trading/intervals'
import {
  insertLiveBar,
  LiveBarBuilder,
  LiveBarClock,
  type LiveBarUpdate,
  removeLiveBar,
  upsertLiveBar,
} from '@/lib/trading/liveBars'
import { applySessionHours, sessionCalendarFor } from '@/lib/trading/sessionHours'
import type { AppMode } from '@/stores/themeStore'
import { CHART_BRANDING } from '@/sudarshan/brand'
import {
  type CombinedTick,
  ComputedFeed,
  type ComputedSpec,
  computedKey,
  LegPrices,
  specProblem,
} from './computed'
import { intervalLabel } from './prefs'

/** Away longer than this, the candles traded meanwhile are read on coming back. */
export const COMPUTED_AWAY_MS = 5_000

/** Bars in view when the chart opens. */
export const COMPUTED_VISIBLE_BARS = 60

export interface ComputedChartCallbacks {
  /** Loading, drawn, no bars, or failed: the same states and words as the phone chart. */
  onLoad(state: ChartStateView): void
  /** The sum, on every combined tick. */
  onValue?(tick: CombinedTick): void
}

export interface ComputedChartOptions {
  container: HTMLElement
  apiKey: string
  appMode: AppMode
  callbacks: ComputedChartCallbacks
  /** What the chart is called in its states ("NIFTY 25,900 straddle"). */
  name?: string
  /** UTC seconds; the phone's clock unless the screen has a better one. */
  now?: () => number
}

export class ComputedChart {
  private readonly apiKey: string
  private readonly cb: ComputedChartCallbacks
  private readonly container: HTMLElement
  private readonly now: () => number
  private appMode: AppMode
  private name: string
  private chart: Chart
  private price: SeriesApi
  private readonly feed: ComputedFeed
  private readonly data: DataLoadingController
  private readonly clock: LiveBarClock
  private builder: LiveBarBuilder | null = null
  private prices: LegPrices | null = null
  private bars: Bar[] = []
  private spec: ComputedSpec | null = null
  private key = ''
  private interval = '5m'
  private ticket = 0
  private loadState: ChartStateView = CHART_READY
  private loadingOlder = false
  private hiddenAt: number | null = null
  private destroyed = false
  private readonly offs: Array<() => void> = []
  /** History as the candle clock repairs it: never read while the page is hidden. */
  private readonly whenShown = {
    bars: () => this.data.bars(),
    refresh: () =>
      document.visibilityState === 'hidden' ? Promise.resolve() : this.data.refresh(),
  }

  constructor(opts: ComputedChartOptions) {
    this.apiKey = opts.apiKey
    this.cb = opts.callbacks
    this.container = opts.container
    this.appMode = opts.appMode
    this.name = opts.name ?? 'Computed chart'
    this.now = opts.now ?? (() => Date.now() / 1000)
    ;({ chart: this.chart, price: this.price } = this.buildChart())
    const rest = new OpenAlgoDataFeed({ baseUrl: '', apiKey: this.apiKey })
    this.feed = new ComputedFeed(withBarCache(rest, { ttlMs: 10 * 60_000 }))
    this.data = new DataLoadingController(this.feed, {
      now: () => Math.floor(this.now()),
      // The candle clock asks after each close; nothing here reads on a timer.
      pollIntervalMs: 0,
      refreshOnBarClose: true,
      refreshOnGap: true,
      refreshWindowBars: 5,
      pageSize: 500,
      maxEmptyPages: 4,
      maxBars: 20_000,
    })
    this.offs.push(this.data.subscribe((snapshot) => this.applySnapshot(snapshot)))
    this.clock = new LiveBarClock({
      builder: () => (this.destroyed ? null : this.builder),
      onBar: (update) => this.applyLiveBar(update),
      history: () => this.whenShown,
      now: this.now,
    })
    document.addEventListener('visibilitychange', this.onVisibility)
  }

  /**
   * Show the sum of two instruments at an interval. Resolves once history is drawn, or has
   * failed; either way the line then moves with the ticks the screen hands over.
   */
  async loadComputed(spec: ComputedSpec, chip: string, name?: string): Promise<void> {
    if (this.destroyed) return
    const ticket = ++this.ticket
    if (name) this.name = name
    this.clock.stop()
    this.builder = null
    this.prices = null
    this.bars = []
    this.interval = chip === '1D' ? 'D' : chip
    const label = intervalLabel(this.interval)
    const problem = specProblem(spec)
    if (problem) {
      this.spec = null
      this.setLoad(chartNoData(this.name, label, problem))
      return
    }
    this.spec = spec
    this.key = computedKey(spec)
    this.feed.setSpec(spec)
    this.setLoad({ kind: 'loading', symbol: this.name, interval: label })
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price } = this.buildChart())
    const exchange = sharedExchange(spec)
    applySessionHours(this.chart, exchange, this.apiKey, null)
    const to = Math.floor(this.now())
    let bars: readonly Bar[] = []
    let failure: unknown = null
    try {
      bars = await this.data.load({
        symbol: this.key,
        exchange: '',
        interval: this.interval,
        from: to - lookbackDays(this.interval) * 86400,
        to,
      })
    } catch (error) {
      failure = error
    }
    if (ticket !== this.ticket || this.destroyed) return
    failure ??= bars.length ? null : this.data.getState().error
    if (failure)
      this.setLoad(chartLoadFailed(this.name, label, String((failure as Error)?.message ?? '')))
    else if (!bars.length) this.setLoad(chartNoData(this.name, label))
    else this.setLoad(CHART_READY)
    this.startLive(spec, exchange, to, ticket)
  }

  /**
   * One leg's price, from the screen's own subscription: `leg` is its place in the spec, `time`
   * the exchange's time for it in UTC seconds when the feed sent one.
   */
  tick(leg: number, price: number, time: number | null): void {
    if (this.destroyed || !this.prices) return
    const combined = this.prices.set(leg, price, time)
    if (!combined) return
    this.cb.onValue?.(combined)
    const update = this.builder?.onTick({
      time: combined.time ?? this.now(),
      price: combined.price,
    })
    if (update) this.applyLiveBar(update)
  }

  /** The screen's connection came back: what traded meanwhile was never built from ticks. */
  resync(): void {
    if (this.destroyed || !this.spec) return
    this.builder?.resync()
    if (document.visibilityState !== 'hidden') void this.data.refresh()
  }

  /** Analyze mode came on or went off: redraw in its colours, keeping the bars and the view. */
  setAppMode(mode: AppMode): void {
    if (mode === this.appMode || this.destroyed) return
    this.appMode = mode
    const range = this.bars.length ? this.chart.getVisibleLogicalRange() : null
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price } = this.buildChart())
    if (this.spec) applySessionHours(this.chart, sharedExchange(this.spec), this.apiKey, null)
    if (!this.bars.length) return
    this.price.setData(this.bars)
    if (range) this.chart.setVisibleLogicalRange(range)
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    document.removeEventListener('visibilitychange', this.onVisibility)
    for (const off of this.offs.splice(0)) off()
    this.clock.stop()
    this.data.destroy()
    this.chart.destroy()
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
    if (this.destroyed) return
    // Android stops timers while the phone sleeps: the candle clock is aimed again from now.
    if (this.builder?.hasSession()) this.clock.start()
    if (hiddenAt !== null && Date.now() - hiddenAt > COMPUTED_AWAY_MS) this.resync()
  }

  private buildChart(): { chart: Chart; price: SeriesApi } {
    this.container.innerHTML = ''
    const chart = createChart(this.container, {
      theme: buildChartTheme('light', this.appMode),
      priceAxisWidth: 60,
      navigation: { defaultVisibleBars: COMPUTED_VISIBLE_BARS },
      axisChrome: { barCountdown: false, clock: () => this.now() },
      shortcuts: false,
      ...chartMotionOptions(),
      branding: CHART_BRANDING,
      legendOffset: { top: -10_000, left: 8 },
    })
    const price = chart.addSeries('line')
    chart.setHistoryLoader(() => void this.loadOlder())
    chart.setPriceScaleOptions({ minMove: 0.05 })
    return { chart, price }
  }

  private setLoad(state: ChartStateView): void {
    this.loadState = state
    this.cb.onLoad(state)
  }

  private applySnapshot(snapshot: DataLoadingSnapshot): void {
    const request = snapshot.request
    if (
      this.destroyed ||
      !request ||
      !this.spec ||
      request.symbol !== this.key ||
      request.interval !== this.interval
    )
      return
    if (snapshot.reason === 'live' || snapshot.reason === 'state') return
    if (!snapshot.bars.length) return
    let next = [...snapshot.bars]
    if (this.builder) next = this.builder.mergeSnapshot(next, Math.floor(this.now()))
    const before = snapshot.reason === 'prepend' ? this.chart.getVisibleLogicalRange() : null
    const inserted = next.length - this.bars.length
    this.bars = next
    this.price.setData(this.bars)
    if (snapshot.reason === 'prepend' && before && inserted > 0)
      this.chart.setVisibleLogicalRange({ from: before.from + inserted, to: before.to + inserted })
  }

  private async loadOlder(): Promise<void> {
    if (this.loadingOlder || this.destroyed || !this.spec) {
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
   * The forming bar, seeded from history's last bar, each leg starting at the close its own
   * history ended on, and the exchange's hours (and an expiring contract's own stop) once they
   * arrive, for the clock that opens candles.
   */
  private startLive(spec: ComputedSpec, exchange: string, readAt: number, ticket: number): void {
    const last = this.bars[this.bars.length - 1] ?? this.data.bars().at(-1)
    const builder = LiveBarBuilder.supports(this.interval)
      ? new LiveBarBuilder({ interval: this.interval, anchorSec: last?.time })
      : null
    if (builder && last) builder.seed(last, readAt)
    this.builder = builder
    const prices = new LegPrices(spec.legs.length)
    this.feed.lastCloses.forEach((close, leg) => {
      if (close !== null) prices.seed(leg, close)
    })
    this.prices = prices
    if (!builder || !exchange) return
    const mine = () => !this.destroyed && ticket === this.ticket && this.builder === builder
    void sessionCalendarFor(exchange, this.apiKey)
      .then((calendar) => {
        if (!mine() || !calendar) return null
        builder.setSession(calendar)
        this.clock.start()
        return instrumentSessionFor(spec.legs[0].symbol, exchange)
      })
      .then((facts) => {
        if (!mine() || !facts || facts.stopsAt === null) return null
        builder.setClosing(facts.closing)
        const stopsAt = facts.stopsAt
        return sessionCalendarFor(exchange, this.apiKey, stopsAt).then((own) => {
          if (!mine() || !own) return
          if (!this.chart.isDestroyed) this.chart.setSessionCalendar(own)
          builder.setSession(own)
          this.clock.start()
        })
      })
      .catch(() => {
        /* live on ticks alone, without a clock */
      })
  }

  private applyLiveBar(update: LiveBarUpdate): void {
    let redraw = false
    if (update.replaces !== undefined) redraw = removeLiveBar(this.bars, update.replaces)
    if (update.previous) {
      const placed = insertLiveBar(this.bars, update.previous)
      if (placed === 'insert') redraw = true
      else if (!redraw) this.price.update(update.previous)
    }
    if (upsertLiveBar(this.bars, update.bar) === 'skip') {
      if (redraw) this.price.setData(this.bars)
      return
    }
    if (this.loadState.kind === 'empty' && this.spec) this.setLoad(CHART_READY)
    if (!update.placeholder)
      this.data.pushBar(update.bar, update.provisional ? { provisional: true } : undefined)
    if (redraw) this.price.setData(this.bars)
    else this.price.update(update.bar)
  }
}

/** The legs' exchange when they share one: its hours then open the candles; else none do. */
function sharedExchange(spec: ComputedSpec): string {
  const all = new Set(spec.legs.map((leg) => leg.exchange))
  return all.size === 1 ? spec.legs[0].exchange : ''
}
