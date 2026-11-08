// SUDARSHAN M3.11: the dashboard's Portfolio card (design 8.3). One row per tab: the Upstox
// account's holdings, then the pinned custom tabs (at most 4). Each row: name, value, today (arrow,
// sign, %) and overall gain. The service's summary is the first snapshot (read on mount, on a
// reconnect, on coming back and on any change to the portfolio, never on a timer); one feed
// subscription over the union of the rows' listed instruments, at most LIVE_CAP of them (R4),
// moves every row with every tick. The Upstox row is labelled as the real account in Analyze
// mode, because the sandbox has no portfolio of its own. "See all" goes to /portfolio.
import { ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/themeStore'
import { type FeedRow, useBookFeed } from '@/sudarshan/books/feed'
import { useBookSync, useLatestRead } from '@/sudarshan/books/useBookSync'
import { useFrameLines } from '@/sudarshan/live/useFrameLines'
import { portfolioCall } from '@/sudarshan/portfolio/api'
import {
  LIVE_CAP,
  liveInstruments,
  moveTab,
  type Tick,
  tickOf,
} from '@/sudarshan/portfolio/liveTab'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { rupeesText } from '@/sudarshan/portfolio/tab/format'
import type { PortfolioSummary, SummaryEntry } from '@/sudarshan/portfolio/types'
import { holdingNow, holdingsTotals } from '@/sudarshan/portfolio/upstox/live'
import { LiveLine } from './LiveLine'
import { keyOf, type MarketOpen } from './live'

/** Pinned custom tabs shown, at most (design 8.3). */
export const PINNED_SHOWN = 4

/** One row as the card draws it. */
export interface CardRow {
  key: string
  name: string
  note: string | null
  valuePaise: number
  todayPaise: number | null
  todayPct: string | null
  /** Why there is no Today figure, when there is none. */
  todayWords: string | null
  gainPaise: number
  gainPct: string | null
  stale: boolean
}

/** The entries the card shows: the Upstox tab first, then the pinned custom tabs, at most four. */
export function entriesShown(entries: readonly SummaryEntry[]): SummaryEntry[] {
  const upstox = entries.filter((e) => e.tab.system)
  const pinned = entries.filter((e) => !e.tab.system && e.tab.pinned && !e.tab.archived)
  return [...upstox, ...pinned.slice(0, PINNED_SHOWN)]
}

/** Every listed instrument the rows hold, each once, the first LIVE_CAP of them. */
export function instrumentsOf(
  entries: readonly SummaryEntry[]
): Array<{ symbol: string; exchange: string }> {
  const seen = new Map<string, { symbol: string; exchange: string }>()
  const add = (symbol: string | null, exchange: string | null) => {
    if (symbol && exchange && seen.size < LIVE_CAP) {
      const key = keyOf(symbol, exchange)
      if (!seen.has(key)) seen.set(key, { symbol, exchange })
    }
  }
  for (const entry of entries) {
    for (const row of entry.upstox?.holdings ?? []) add(row.symbol, row.exchange)
    for (const i of liveInstruments(entry.live?.rows ?? [], LIVE_CAP)) add(i.symbol, i.exchange)
  }
  return [...seen.values()]
}

export function rowsOf(
  entries: readonly SummaryEntry[],
  ticks: ReadonlyMap<string, Tick>,
  readAt: number,
  analyze: boolean
): CardRow[] {
  const out: CardRow[] = []
  for (const entry of entries) {
    if (entry.tab.system) {
      const part = entry.upstox
      const holdings = part?.holdings ?? []
      const now = holdings.map((row) =>
        holdingNow(
          row,
          row.symbol && row.exchange ? ticks.get(keyOf(row.symbol, row.exchange)) : undefined,
          part?.read_at ?? null
        )
      )
      const totals = holdingsTotals(now)
      const unread = !part || part.state === 'unavailable'
      out.push({
        key: `upstox-${entry.tab.id}`,
        name: analyze ? 'Upstox account (real)' : entry.tab.name,
        note: part?.note ?? null,
        valuePaise: totals.valuePaise,
        todayPaise: unread ? null : totals.todayPaise,
        todayPct: null, // the Upstox tab shows Today in rupees alone, as its own figure
        todayWords: unread
          ? null
          : totals.todayPaise === null
            ? 'Today: not known yet'
            : totals.todayUnknown > 0
              ? `${totals.todayUnknown} without a previous close`
              : null,
        gainPaise: totals.pnlPaise,
        gainPct: totals.pnlPct,
        stale: part?.state === 'stale',
      })
      continue
    }
    const live = entry.live
    if (!live) continue
    const moved = moveTab(live.rows, live.fixed, ticks, readAt, LIVE_CAP)
    const today = moved.totals.today
    out.push({
      key: `tab-${entry.tab.id}`,
      name: entry.tab.name,
      note: null,
      valuePaise: moved.totals.value_paise,
      todayPaise: today.state === 'ok' ? today.paise : null,
      todayPct: today.state === 'ok' ? today.pct : null,
      todayWords:
        today.state === 'ok'
          ? null
          : today.state === 'none'
            ? null
            : today.state === 'pending'
              ? 'Today: waiting for the NAV'
              : 'Today: not known yet',
      gainPaise: moved.totals.overall_gain_paise,
      gainPct: moved.totals.absolute_return_pct,
      stale: false,
    })
  }
  return out
}

const NOT_KNOWN: MarketOpen = () => null

export function PortfolioCard({ marketOpen = NOT_KNOWN }: { marketOpen?: MarketOpen }) {
  const analyze = useThemeStore((s) => s.appMode === 'analyzer')
  // Read as the other cards read: once on mount, again on a reconnect or on coming back
  // (useBookSync), never on a timer; a later answer never overwrites a newer read.
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [pending, setPending] = useState(true)
  const alive = useRef(true)
  const beginRead = useLatestRead()
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  const load = useCallback(async () => {
    const isLatest = beginRead()
    try {
      const result = await portfolioCall('summary', {})
      if (!alive.current || !isLatest()) return
      setSummary(result.enabled ? result.data : null)
    } catch {
      if (alive.current && isLatest()) setSummary(null)
    } finally {
      if (alive.current && isLatest()) setPending(false)
    }
  }, [beginRead])
  useEffect(() => {
    load()
  }, [load])
  useBookSync(load, { events: [], resyncMs: null })
  const entries = useMemo(() => entriesShown(summary?.entries ?? []), [summary])
  const instruments = useMemo(() => instrumentsOf(entries), [entries])
  const { lines, feed } = useFrameLines({
    instruments,
    mode: 'LTP',
    enabled: instruments.length > 0,
  })

  // The newest good tick of each instrument: a price that is no price leaves the last one.
  const good = useRef(new Map<string, Tick>())
  const ticks = useMemo(() => {
    const wanted = new Set(instruments.map((i) => keyOf(i.symbol, i.exchange)))
    for (const key of [...good.current.keys()]) if (!wanted.has(key)) good.current.delete(key)
    for (const [key, line] of lines) {
      const tick = tickOf(line)
      if (tick) good.current.set(key, tick)
    }
    return new Map(good.current)
  }, [lines, instruments])

  const rows = useMemo(
    () => rowsOf(entries, ticks, summary?.read_at ?? Number.MAX_SAFE_INTEGER, analyze),
    [entries, ticks, summary, analyze]
  )
  const feedRows: FeedRow[] = useMemo(
    () =>
      instruments.map((i) => {
        const tick = ticks.get(keyOf(i.symbol, i.exchange))
        return {
          exchange: i.exchange,
          ltp: tick?.ltp,
          _at: tick?.at ?? null,
          _live: tick?.live === true,
        }
      }),
    [instruments, ticks]
  )
  const stamps = [...ticks.values()]
  const lastTickAt = stamps.reduce<number | null>(
    (best, t) => (t.live && (best === null || t.at > best) ? t.at : best),
    null
  )
  const lastUpdateAt = stamps.reduce<number | null>(
    (best, t) => (best === null || t.at > best ? t.at : best),
    null
  )
  const book = useBookFeed(
    feedRows,
    { lastTickAt, lastUpdateAt, due: feedRows.length, connected: feed.connected },
    marketOpen
  )
  const allStale =
    (book.line.kind === 'delayed' || book.line.kind === 'connecting') && book.line.stale

  if (pending || summary === null || rows.length === 0) return null

  return (
    <section
      aria-labelledby="sd-portfolio-title"
      className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 pt-3 pb-3.5 shadow-[var(--shadow-card)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="sd-portfolio-title" className="text-[0.9375rem] font-bold">
          Portfolio
        </h2>
        <Link
          to="/portfolio"
          className="-my-2 -mr-2 inline-flex min-h-12 items-center gap-0.5 rounded-lg px-2 text-[0.84375rem] font-semibold text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all
          <ChevronRight aria-hidden="true" className="size-4" />
        </Link>
      </div>
      <ul
        aria-label="Portfolio tabs"
        className="flex flex-col divide-y divide-[color:var(--divider)]"
      >
        {rows.map((row) => {
          const stale = allStale || row.stale
          return (
            <li key={row.key} className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-px py-2">
              <span className="truncate text-[0.9375rem] font-semibold">{row.name}</span>
              <span
                className={cn(
                  'text-right text-[0.9375rem] font-semibold tabular-nums',
                  stale && 'text-muted-foreground'
                )}
              >
                {rupeesText(row.valuePaise)}
              </span>
              <span className="flex flex-wrap items-center gap-x-3 text-xs text-[color:var(--text-secondary)]">
                <span>
                  Today{' '}
                  {row.todayPaise !== null ? (
                    <Signed paise={row.todayPaise} pct={row.todayPct} stale={stale} />
                  ) : (
                    <span>{row.todayWords ?? 'none'}</span>
                  )}
                </span>
                {row.note && <span>{row.note}</span>}
              </span>
              <span className="text-right text-xs text-[color:var(--text-secondary)]">
                Overall <Signed paise={row.gainPaise} pct={row.gainPct} stale={stale} />
              </span>
            </li>
          )
        })}
      </ul>
      <LiveLine line={book.line} now={book.now} className="self-start" />
    </section>
  )
}
