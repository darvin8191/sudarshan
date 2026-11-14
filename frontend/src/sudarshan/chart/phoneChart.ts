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
  getIndicator,
  hasIndicator,
  INDICATOR_SOURCES,
  type IndicatorApi,
  InvalidationLevel,
  indicatorDefaults,
  type LtpEvent,
  type MarketDepth,
  OpenAlgoDataFeed,
  OpenAlgoWsFeed,
  registeredIndicators,
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
import { watchIstDay } from '@/lib/trading/dayWatch'
import { instrumentSessionFor } from '@/lib/trading/instrumentSession'
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
import {
  type CatalogueEntry,
  legendValues,
  type Placement,
  type StudyEntry,
  type StudyFacts,
  type StudyForm,
  skipReason,
  studyFields,
  VOLUME_MA,
  volumeMaAt,
  volumeMaLength,
} from './phoneStudies'
import { type ChartStyle, DEFAULT_STYLE, intervalLabel, styleOf } from './prefs'

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
  /** Open interest, on a contract, when the frame or quote carried it (C9). */
  oi?: number
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
  /** The studies on the chart changed: a set applied, a chart built anew (C3). */
  onStudies?(): void
  /** The five-level book, on every depth frame of the charted instrument (C9). */
  onDepth?(depth: MarketDepth): void
}

/** One legend line (C3): a study's name and settings, its values at a bar, or why it is not drawn. */
export interface LegendRow {
  key: string
  title: string
  params: string
  values: string[]
  /** The pane it belongs to, and that pane's top in the chart's pixels. */
  pane: number
  top: number
  /** Why it is not drawn here, or why it could not compute. */
  note?: string
}

export interface PhoneChartOptions {
  container: HTMLElement
  apiKey: string
  wsUrl: string
  appMode: AppMode
  /** Candles unless the phone chose another style (C2). */
  style?: ChartStyle
  callbacks: PhoneChartCallbacks
}

export class PhoneChart {
  private readonly apiKey: string
  private readonly cb: PhoneChartCallbacks
  private readonly container: HTMLElement
  private appMode: AppMode
  private style: ChartStyle
  /** The transform tier is loaded: Heikin Ashi can be drawn. */
  private transformReady = false
  /** The indicator set (C3), applied to every chart this host builds. */
  private studies: StudyEntry[] = []
  /** Each entry's study on the current chart, or null where it is skipped or drawn by the host. */
  private studyApis: Array<IndicatorApi | null> = []
  private indicatorsReady = false
  /** The engine's own ids, from its indicator tier's list (C4). */
  private builtIns: Set<string> | null = null
  /** The volume pane's moving average, when the set holds one and the instrument trades. */
  private volumeMa: SeriesApi | null = null
  /** The instrument's tick, for the price axis and a study's `ctx.tickSize`. */
  private tick = 0
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
  /** The stream is down, so quotes stand in for it while the page is shown. */
  private streamDown = false
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
  /** SUDARSHAN (review M3): when the loaded contract stops trading today, epoch ms, from the server. */
  private stopsAt: number | null = null
  /** Stops the watch for the date in India to change (review N3). */
  private dayWatch: (() => void) | null = null
  private destroyed = false
  private readonly offs: Array<() => void> = []
  /** History as the candle clock repairs it: never read while the page is hidden. */
  private readonly whenShown = {
    bars: () => this.data.bars(),
    refresh: () =>
      document.visibilityState === 'hidden' ? Promise.resolve() : this.data.refresh(),
  }

  constructor(opts: PhoneChartOptions) {
    this.apiKey = opts.apiKey
    this.cb = opts.callbacks
    this.container = opts.container
    this.appMode = opts.appMode
    this.style = opts.style ?? DEFAULT_STYLE
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
      // Hidden, nothing is read: the wake reads the whole gap instead (C13).
      history: () => this.whenShown,
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
    // A saved Heikin Ashi needs its tier before the first build (C2).
    if (styleOf(this.style).transform) await this.loadTransformTier()
    if (ticket !== this.ticket || this.destroyed) return
    const same = this.sym?.symbol === symbol && this.sym.exchange === exchange
    this.stopLive()
    if (!same) {
      this.unsubscribe()
      this.streamed = false
      this.stopsAt = null
    }
    this.sym = { symbol, exchange }
    this.interval = apiInterval(chip)
    this.bars = []
    const label = intervalLabel(this.interval)
    this.setLoad({ kind: 'loading', symbol, interval: label })
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price, volume: this.volume } = this.buildChart(exchange))
    this.chart.setDataContext({ symbol, exchange, interval: this.interval })
    applySessionHours(this.chart, exchange, this.apiKey, this.stopsAt)
    this.applyStudies()
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
    if (failure) this.setLoad(chartLoadFailed(symbol, label, cleanError(failure)))
    else if (!bars.length) this.setLoad(chartNoData(symbol, label))
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
    this.rebuild()
  }

  /**
   * Draw in another style (C2), keeping the candles and the view. Heikin Ashi
   * loads the engine's transform tier the first time it is chosen.
   */
  async setStyle(style: ChartStyle): Promise<void> {
    if (style === this.style || this.destroyed) return
    this.style = style
    if (styleOf(style).transform) await this.loadTransformTier()
    if (this.destroyed || this.style !== style) return
    this.rebuild()
  }

  private async loadTransformTier(): Promise<void> {
    if (this.transformReady) return
    try {
      await import('openalgo-charts/transform')
      this.transformReady = true
    } catch {
      /* drawn as candles until the next choice or load asks again */
    }
  }

  /**
   * The indicator set (C3), for this chart and every one built after it. The
   * engine's indicator tier is imported the first time a set needs it.
   */
  async setStudies(set: readonly StudyEntry[]): Promise<void> {
    if (this.destroyed) return
    const next = set.map((entry) => ({ id: entry.id, settings: { ...entry.settings } }))
    // The same studies with other settings (C4): changed where they stand, nothing re-added.
    const same =
      this.studyApis.length === next.length &&
      next.every((entry, i) => entry.id === this.studies[i]?.id)
    this.studies = next
    if (same) {
      next.forEach((entry, i) => {
        const api = this.studyApis[i]
        if (api) api.setSettings(this.settingsOf(entry), { force: true })
      })
      if (this.bars.length) this.draw()
      this.cb.onStudies?.()
      return
    }
    if (next.some((entry) => entry.id !== VOLUME_MA)) await this.loadIndicatorTier()
    // A saved set naming the owner's own studies loads just the files they come from (C4).
    const missing = next.map((e) => e.id).filter((id) => id !== VOLUME_MA && !hasIndicator(id))
    if (this.indicatorsReady && missing.length) await this.restoreMine(missing)
    if (this.destroyed) return
    this.applyStudies()
    if (this.bars.length) this.draw()
  }

  /** A study's settings over its defaults, so a key cleared from the set returns to its default. */
  private settingsOf(entry: StudyEntry): Record<string, unknown> {
    return hasIndicator(entry.id)
      ? { ...indicatorDefaults(getIndicator(entry.id)), ...entry.settings }
      : entry.settings
  }

  /**
   * The owner's own studies a saved set names: the JavaScript files that
   * register them, then the OpenScript studies when one is a script, as the
   * terminal restores a layout. Failures are said when the sheet opens.
   */
  private async restoreMine(ids: string[]): Promise<void> {
    try {
      const { ensureCustomIndicators } = await import('@/lib/trading/customIndicators')
      await ensureCustomIndicators(ids)
      const { fileForScriptId } = await import('@/lib/trading/openscriptFiles')
      if (ids.some((id) => fileForScriptId(id) !== null)) {
        const { loadOpenScriptStudies } = await import('@/lib/trading/openscriptStudies')
        await loadOpenScriptStudies()
      }
    } catch {
      /* skipped with a line until the next load */
    }
  }

  /**
   * Every study the owner can add (C4): the engine's built-ins and his own,
   * the JavaScript indicators and then the OpenScript studies on the server,
   * loaded in that order after the built-in tier, exactly as the terminal
   * loads them, so one of his reusing a built-in id overrides it the same way.
   * The loaders' own sentences come back for a file that would not load.
   */
  async loadMyScripts(): Promise<string[]> {
    if (!(await this.loadIndicatorTier())) return []
    const problems: string[] = []
    try {
      const { loadCustomIndicators } = await import('@/lib/trading/customIndicators')
      const custom = await loadCustomIndicators()
      for (const e of custom.errors) problems.push(`${e.file}: ${e.message}`)
      const { loadOpenScriptStudies } = await import('@/lib/trading/openscriptStudies')
      const scripts = await loadOpenScriptStudies()
      for (const e of scripts.errors) problems.push(`${e.file}: ${e.message}`)
    } catch {
      problems.push('Your scripts could not be read. Check the connection, then open this again.')
    }
    return problems
  }

  /** The catalogue the sheet searches: built-ins, and the owner's own marked so, by name. */
  catalogue(): CatalogueEntry[] {
    if (!this.indicatorsReady) return []
    const builtIns = this.builtIns ?? new Set<string>()
    return registeredIndicators()
      .map((d) => ({
        id: d.id,
        name: d.name,
        category: d.category ?? 'Other',
        mine: !builtIns.has(d.id),
      }))
      .sort((a, b) => a.name.localeCompare(b.name))
  }

  /** The settings form of the set's study at `index` (C4), from the engine's own descriptor. */
  studyForm(index: number): StudyForm | null {
    const entry = this.studies[index]
    if (!entry) return null
    if (entry.id === VOLUME_MA)
      return {
        name: 'Volume moving average',
        fields: [{ key: 'length', type: 'number', label: 'Length', min: 2, max: 500, step: 1 }],
        values: { length: volumeMaLength(entry.settings) },
      }
    if (!this.indicatorsReady || !hasIndicator(entry.id)) return null
    const descriptor = getIndicator(entry.id)
    return {
      name: descriptor.name,
      fields: studyFields(descriptor.inputs ?? [], INDICATOR_SOURCES),
      values: this.settingsOf(entry),
    }
  }

  /** What the registry says of a study: its name and where it draws; null for an id it does not hold. */
  studyFacts(id: string): StudyFacts | null {
    if (id === VOLUME_MA) return { name: 'Volume moving average', placement: 'host' }
    if (!this.indicatorsReady || !hasIndicator(id)) return null
    const descriptor = getIndicator(id)
    return {
      name: descriptor.name,
      placement: (descriptor.placement === 'pane' ? 'pane' : 'onchart') as Placement,
      category: descriptor.category,
    }
  }

  /** The tier the studies come from, imported once, when a set or the sheet first needs it. */
  async loadIndicatorTier(): Promise<boolean> {
    if (this.indicatorsReady) return true
    try {
      const tier = await import('openalgo-charts/indicators')
      // The tier's own list, so a script another page loaded first is never taken for a built-in.
      this.builtIns = new Set(tier.BUILTIN_INDICATORS.map((d) => d.id))
      this.indicatorsReady = true
    } catch {
      /* the next set or sheet asks again */
    }
    return this.indicatorsReady
  }

  /** The instrument's tick: the price axis is written in it, and a study reads it. */
  setTick(tick: number): void {
    if (!(tick > 0) || this.destroyed) return
    this.tick = tick
    this.chart.setPriceScaleOptions({ minMove: tick })
  }

  /**
   * One legend line per study (C3), with its values at the bar of `time`, or
   * at the last bar. A study the engine could not compute says so in place of
   * values.
   */
  legendRows(time: number | null = null): LegendRow[] {
    const held = time === null ? -1 : this.bars.findIndex((bar) => bar.time === time)
    const at = held >= 0 ? held : this.bars.length - 1
    const decimals = (pane: number) => (pane === 0 ? Math.max(2, decimalsOf(this.tick)) : 2)
    return this.studies.map((entry, i) => {
      const api = this.studyApis[i]
      const key = `${i}:${entry.id}`
      if (entry.id === VOLUME_MA) {
        const top = this.chart.plotRect(1)?.top ?? 0
        if (!this.volumeMa)
          return {
            key,
            title: 'Vol MA',
            params: '',
            values: [],
            pane: 1,
            top,
            note: this.skip(entry) ?? undefined,
          }
        const length = volumeMaLength(entry.settings)
        const value = volumeMaAt(
          this.bars.map((b) => b.volume),
          at,
          length
        )
        const values = [value === null ? '—' : compactVolume.format(value)]
        return { key, title: 'Vol MA', params: String(length), values, pane: 1, top }
      }
      if (!api)
        return {
          key,
          title: this.studyFacts(entry.id)?.name ?? entry.id,
          params: '',
          values: [],
          pane: 0,
          top: 0,
          note: this.skip(entry) ?? undefined,
        }
      const legend = api.legend()?.options()
      const pane = api.paneIndex
      const status = api.dataStatus()
      const row: LegendRow = {
        key,
        title: legend?.title ?? api.name,
        params: legend?.params ?? '',
        values: [],
        pane,
        top: this.chart.plotRect(pane)?.top ?? 0,
      }
      if (status?.state === 'error') return { ...row, note: 'Could not compute on this chart.' }
      const plots = getIndicator(entry.id).plots.map((plot) => plot.key)
      return { ...row, values: legendValuesOf(api, plots, at, decimals(pane)) }
    })
  }

  private skip(entry: StudyEntry): string | null {
    const sym = this.sym
    if (!sym) return null
    return skipReason(entry.id, this.studyFacts(entry.id), {
      exchange: sym.exchange,
      traded: !isQuoteOnly(sym.exchange),
    })
  }

  /**
   * Put the set on the chart as it is now: what it had goes, each entry is
   * added in order, and one that cannot run on this instrument is skipped. The
   * engine's own legend rows and their small buttons stay hidden: the page
   * draws the legend, and the sheet holds the settings and Remove.
   */
  private applyStudies(): void {
    for (const api of this.studyApis) {
      try {
        if (api) this.chart.removeIndicator(api.id, { force: true })
      } catch {
        /* went with the chart it was on */
      }
    }
    this.studyApis = []
    this.volumeMa?.remove()
    this.volumeMa = null
    for (const entry of this.studies) {
      if (entry.id === VOLUME_MA) {
        if (this.volume && !this.volumeMa) this.volumeMa = this.addVolumeMa()
        this.studyApis.push(null)
        continue
      }
      if (!this.indicatorsReady || this.skip(entry)) {
        this.studyApis.push(null)
        continue
      }
      try {
        const api = this.chart.addIndicator(entry.id, entry.settings, {
          policy: { removable: false, configurable: false, movable: false },
        })
        api.legend()?.setOptions({ visible: false })
        this.studyApis.push(api)
      } catch {
        this.studyApis.push(null)
      }
    }
    this.weighPanes()
    if (!this.destroyed) this.cb.onStudies?.()
  }

  /**
   * Heights on a phone: the candles keep most of it, and a study's pane gets
   * enough to read its line; the volume pane gives a little up when two study
   * panes share the screen.
   */
  private weighPanes(): void {
    const panes = this.chart.panes().length
    const studyPanes = panes - (this.volume ? 2 : 1)
    this.chart.setPaneWeight(0, 4)
    if (this.volume) this.chart.setPaneWeight(1, studyPanes > 1 ? 0.8 : 1)
    for (let p = this.volume ? 2 : 1; p < panes; p++) this.chart.setPaneWeight(p, 1.4)
  }

  private addVolumeMa(): SeriesApi {
    return this.chart.addSeries('line', {
      paneIndex: 1,
      priceFormat: { type: 'volume' },
      style: {
        color: this.chart.theme().axisText,
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      },
    })
  }

  /** The chart built anew for a mode or a style, as /trading does, keeping the bars and the view. */
  private rebuild(): void {
    const sym = this.sym
    if (!sym || this.destroyed) return
    const range = this.bars.length ? this.chart.getVisibleLogicalRange() : null
    this.chart.destroy()
    ;({ chart: this.chart, price: this.price, volume: this.volume } = this.buildChart(sym.exchange))
    this.chart.setDataContext({
      symbol: sym.symbol,
      exchange: sym.exchange,
      interval: this.interval,
    })
    applySessionHours(this.chart, sym.exchange, this.apiKey, this.stopsAt)
    this.applyStudies()
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
    this.dayWatch?.()
    this.dayWatch = null
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

  /**
   * The candle the page's long-press reads under an x: the one drawn, so on
   * Heikin Ashi its own open, high, low and close (one element a bar).
   */
  barAt(x: number): Bar | null {
    const drawn = this.drawnBars()
    if (!drawn.length) return null
    const index = Math.round(this.chart.timeScale.xToIndex(x))
    if (index < 0 || index >= drawn.length) return null
    const bar = drawn[index]
    // The volume traded under it, which a transform does not carry.
    return bar.volume === undefined ? { ...bar, volume: this.bars[index]?.volume } : bar
  }

  /** What is drawn: the bars, or the elements a transform formed from them. */
  private drawnBars(): readonly Bar[] {
    return this.chart.seriesTransform(this.price) ? this.chart.primaryBars() : this.bars
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
      // The countdown only repaints, and nothing is painted or read while hidden.
      this.stopCountdownTimer()
      this.stopFallback()
      return
    }
    const hiddenAt = this.hiddenAt
    this.hiddenAt = null
    // Android stops the clock timers run on while the phone sleeps, so a timer
    // set before the sleep still has the rest of its wait to run after it: the
    // candle clock is aimed again from now, or a candle nobody trades would
    // open up to a bar late (C13).
    if (!this.destroyed && this.builder?.hasSession()) this.clock.start()
    this.syncCountdown()
    if (this.streamDown && !this.destroyed) this.startFallback()
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
      // The page draws the studies' legend (C3): the engine's own rows are
      // hidden, and its "Indicators" toggle, whose 16 px target sits where
      // they would start, is placed above the pane, where it is not drawn.
      legendOffset: { top: -10_000, left: 8 },
    })
    // Heikin Ashi waits for its tier; meanwhile, and should the tier not load, candles.
    const style = styleOf(this.style)
    const transform = style.transform && this.transformReady ? { type: style.transform } : null
    const price = chart.addSeries(style.series, transform ? { transform } : undefined)
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
    if (this.tick > 0) chart.setPriceScaleOptions({ minMove: this.tick })
    // The studies of the chart before went with it.
    this.studyApis = []
    this.volumeMa = null
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
    if (this.volumeMa) {
      const length = this.volumeMaSetting()
      const volumes = this.bars.map((bar) => bar.volume)
      this.volumeMa.setData(
        this.bars.map((bar, i) => {
          const value = volumeMaAt(volumes, i, length)
          return value === null ? { time: bar.time } : { time: bar.time, value }
        })
      )
    }
  }

  private volumeMaSetting(): number {
    const entry = this.studies.find((e) => e.id === VOLUME_MA)
    return volumeMaLength(entry?.settings ?? {})
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
    if (this.volumeMa) {
      const value = volumeMaAt(
        this.bars.map((b) => b.volume),
        i,
        this.volumeMaSetting()
      )
      this.volumeMa.update(value === null ? { time: bar.time } : { time: bar.time, value })
    }
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
    this.askHours(exchange, builder, this.ticket, false)
    // SUDARSHAN (review N3): a chart left open past midnight in India asks the day's facts again.
    this.dayWatch?.()
    this.dayWatch = watchIstDay(() => {
      if (!this.destroyed) this.askHours(exchange, this.builder, this.ticket, true)
    })
  }

  /**
   * The exchange's hours and the instrument's own facts for today (review M1, M2, M3), applied to
   * the live bar and the marker. `again` is a page that was already open when the date changed
   * (review N3): the facts are asked for the new day, and a stop or closing window that belonged
   * to the old one is dropped when the new day has none.
   */
  private askHours(
    exchange: string,
    builder: LiveBarBuilder | null,
    ticket: number,
    again: boolean
  ): void {
    const facts = instrumentSessionFor(this.sym?.symbol ?? '', exchange)
    void sessionCalendarFor(exchange, this.apiKey)
      .then((calendar) => {
        if (this.destroyed || ticket !== this.ticket || !calendar) return facts
        this.status.setCalendar(calendar)
        if (again && !this.chart.isDestroyed) this.chart.setSessionCalendar(calendar)
        if (!builder || this.builder !== builder) return facts
        builder.setSession(calendar)
        this.clock.start()
        this.syncCountdown()
        return facts
      })
      .then((found) => {
        if (this.destroyed || ticket !== this.ticket || !found) return
        // A closing print after the cash close goes to the day's last candle.
        if (builder && this.builder === builder) builder.setClosing(found.closing)
        if (found.stopsAt === null) {
          if (this.stopsAt !== null) {
            this.stopsAt = null
            this.status.setStop(null)
          }
          return
        }
        // An expiring contract's own stop (review M3): no candle, countdown or "Stale" after it.
        this.stopsAt = found.stopsAt
        this.status.setStop(found.stopsAt / 1000)
        return sessionCalendarFor(exchange, this.apiKey, found.stopsAt).then((own) => {
          if (this.destroyed || ticket !== this.ticket || !own) return
          this.status.setCalendar(own)
          if (!this.chart.isDestroyed) this.chart.setSessionCalendar(own)
          if (!builder || this.builder !== builder) return
          builder.setSession(own)
          this.clock.start()
          this.syncCountdown()
        })
      })
  }

  private stopLive(): void {
    this.dayWatch?.()
    this.dayWatch = null
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
      this.cb.onTick({
        ltp,
        time: null,
        tradeTime: null,
        at: Date.now(),
        polled: true,
        ...(typeof quote?.oi === 'number' && quote.oi > 0 ? { oi: quote.oi } : {}),
      })
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
      this.streamDown = false
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
    this.cb.onDepth?.(depth)
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
      ...(tick.oi !== undefined && tick.oi > 0 ? { oi: tick.oi } : {}),
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
    this.streamDown = true
    // Nobody reads a hidden page: the wake tests the stream and reads the gap (C13).
    if (this.fallback || document.visibilityState === 'hidden') return
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
      oi: typeof quote.oi === 'number' ? quote.oi : undefined,
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

const compactVolume = new Intl.NumberFormat('en-IN', {
  notation: 'compact',
  maximumFractionDigits: 2,
})

/** Decimals a tick is written with: 0.05 has two. */
function decimalsOf(tick: number): number {
  if (!(tick > 0)) return 0
  const text = String(tick)
  return text.includes('.') ? text.split('.')[1].length : 0
}

/** A study's values at a bar, from its own columns, at a pane's decimals. */
function legendValuesOf(
  api: IndicatorApi,
  plots: readonly string[],
  index: number,
  decimals: number
) {
  return legendValues(api.values(), plots, index, decimals)
}
