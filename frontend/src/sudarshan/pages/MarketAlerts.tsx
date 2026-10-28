// SUDARSHAN M10.3: the Alerts page, phone first. The owner's market alerts as the alert service
// holds them (blueprints/sudarshan_alerts.py, contract 1.1), kept current by what it pushes
// (src/sudarshan/alerts/marketAlerts.ts), with each card's price from the tab's own tick stream.
// At /market-alerts because upstream owns /alerts, for the /trading chart's own alerts.
import { AlertTriangle, KeyRound, LoaderCircle, Plus, RefreshCw, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useMemo } from 'react'
import { Link } from 'react-router'
import type { AlertRow } from '@/api/marketAlerts'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { AlertCard } from '@/sudarshan/alerts/AlertCard'
import { AlertFormSheet } from '@/sudarshan/alerts/AlertForm'
import { AlertSheet } from '@/sudarshan/alerts/AlertSheet'
import { HistoryList } from '@/sudarshan/alerts/HistoryList'
import { LineText } from '@/sudarshan/alerts/lines'
import { needsQuote, segmentOpen } from '@/sudarshan/alerts/measure'
import { SettingsSheet } from '@/sudarshan/alerts/SettingsSheet'
import { type AlertTab, useAlertOverlay } from '@/sudarshan/alerts/useAlertOverlay'
import { useMarketAlertsState } from '@/sudarshan/alerts/useMarketAlertsState'
import { useSocketUp } from '@/sudarshan/alerts/useSocketUp'
import {
  EmptyCard,
  LoadingCards,
  OUTLINED,
  STALE,
  StaleBookNote,
} from '@/sudarshan/books/cards/parts'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { keyOf } from '@/sudarshan/dashboard/live'
import { type Instrument, useFrameLines } from '@/sudarshan/live/useFrameLines'

/** The most instruments the page follows tick by tick (cross-design R4). */
export const LIVE_CAP = 100

const NO_ROWS: AlertRow[] = []

const HEADER_BUTTON =
  'inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

const newestFirst = (a: AlertRow, b: AlertRow) => b.id - a.id

/** Active first, then Paused, newest first in each; the Stopped tab, latest stopped first. */
function tabsOf(alerts: AlertRow[]): Record<'active' | 'stopped', AlertRow[]> {
  const active = alerts.filter((row) => row.status === 'active').sort(newestFirst)
  const paused = alerts.filter((row) => row.status === 'paused').sort(newestFirst)
  const stopped = alerts
    .filter((row) => row.status === 'stopped')
    .sort((a, b) => b.updated_at - a.updated_at || newestFirst(a, b))
  return { active: [...active, ...paused], stopped }
}

/** The tab's instruments, each once, in card order, up to the cap. */
function followedOf(rows: AlertRow[]): { list: Instrument[]; keys: Set<string>; capped: boolean } {
  const keys = new Set<string>()
  const list: Instrument[] = []
  let capped = false
  for (const row of rows) {
    const key = keyOf(row.symbol, row.exchange)
    if (keys.has(key)) continue
    if (keys.size >= LIVE_CAP) {
      capped = true
      continue
    }
    keys.add(key)
    list.push({ symbol: row.symbol, exchange: row.exchange })
  }
  return { list, keys, capped }
}

export default function MarketAlerts() {
  const query = useMarketAlertsState()
  const state = query.data
  const apiKey = useAuthStore((auth) => auth.apiKey)
  const { overlay, tab, open, close, setTab } = useAlertOverlay()
  const now = useNow()

  const alerts = state?.alerts ?? NO_ROWS
  const tabs = useMemo(() => tabsOf(alerts), [alerts])
  const rows = tab === 'history' ? NO_ROWS : tabs[tab]
  const followed = useMemo(() => followedOf(rows), [rows])
  const { lines, feed } = useFrameLines({
    instruments: followed.list,
    mode: rows.some(needsQuote) ? 'Quote' : 'LTP',
    enabled: apiKey !== null,
  })

  // An overlay that cannot be shown (an alert deleted meanwhile, a new alert with market alerts
  // off) is closed once the alerts are known, rather than left open over nothing.
  const enabled = state?.enabled === true
  const shownId = overlay?.kind === 'alert' || overlay?.kind === 'edit' ? overlay.id : null
  const selected = shownId === null ? null : (alerts.find((row) => row.id === shownId) ?? null)
  const orphan =
    overlay !== null &&
    state !== undefined &&
    (overlay.kind === 'new' ? !enabled : overlay.kind !== 'settings' && selected === null)
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  const openAlert = useCallback((id: number) => open({ kind: 'alert', id }), [open])
  const editAlert = useCallback((id: number) => open({ kind: 'edit', id }), [open])
  // Saved: the alert's own sheet takes the form's place, so the owner sees what was saved.
  const saved = useCallback((row: AlertRow) => open({ kind: 'alert', id: row.id }), [open])
  const live = (row: AlertRow) => ({
    line: lines.get(keyOf(row.symbol, row.exchange)),
    followed: apiKey !== null && followed.keys.has(keyOf(row.symbol, row.exchange)),
    feed,
    now,
    marketOpen: segmentOpen(state?.segments, row),
  })
  const problem = query.error
  // The engine line is as current as the page can hear: with the app's connection down no push
  // arrives, and with the page's own feed down its cards say Reconnecting. Either way the line
  // stays, grey, "as last read", rather than claim Real-time.
  const socketUp = useSocketUp()
  const deaf =
    socketUp === false ||
    (followed.list.length > 0 && apiKey !== null && feed.epoch > 0 && !feed.connected)

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">Alerts</h1>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Alert settings"
            onClick={() => open({ kind: 'settings' })}
            className={cn(OUTLINED, 'w-12 shrink-0 px-0')}
          >
            <Settings2 aria-hidden="true" className="size-5" />
          </button>
          <button
            type="button"
            disabled={!enabled}
            aria-describedby="sd-alerts-line"
            onClick={() => open({ kind: 'new' })}
            className={cn(HEADER_BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            <Plus aria-hidden="true" className="size-5" />
            New
          </button>
        </div>
      </div>

      <div id="sd-alerts-line" className="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1 px-1">
        {problem ? (
          <span className="inline-flex items-start gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--warning-text)]">
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {problem.message}
          </span>
        ) : state ? (
          <span
            data-heard={deaf ? 'no' : 'yes'}
            className="inline-flex flex-wrap items-start gap-x-1.5"
          >
            <LineText line={state.engine} className={deaf ? STALE : undefined} />
            {deaf && (
              <span className="text-[0.8125rem] font-semibold text-[color:var(--stale)]">
                · as last read
              </span>
            )}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            Reading your alerts
          </span>
        )}
        {problem && (
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
            className={OUTLINED}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn('size-4', query.isFetching && 'animate-spin')}
            />
            Try again
          </button>
        )}
      </div>
      {problem && state && <StaleBookNote text="These are your alerts as last read." />}

      {apiKey === null && (
        <p className="flex flex-wrap items-center gap-x-2 rounded-[1.125rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-4 py-2 text-sm font-semibold text-[color:var(--gold-ink)]">
          <KeyRound aria-hidden="true" className="size-4 shrink-0" />
          Prices and search need an API key.
          <Link
            to="/apikey"
            className="inline-flex min-h-12 items-center text-[color:var(--brand)] underline underline-offset-2"
          >
            Create one under API Key
          </Link>
        </p>
      )}

      <Tabs
        tab={tab}
        counts={{ active: tabs.active.length, stopped: tabs.stopped.length }}
        onSelect={setTab}
      />

      <section
        id="sd-alerts-panel"
        role="tabpanel"
        aria-labelledby={`sd-alerts-tab-${tab}`}
        className="flex flex-col gap-2"
      >
        {tab === 'history' ? (
          <HistoryList now={now} />
        ) : !state ? (
          query.isPending ? (
            <LoadingCards />
          ) : null
        ) : !enabled ? (
          <EmptyCard
            title="No alerts are checked"
            hint="Market alerts are switched off on the server."
          />
        ) : rows.length === 0 ? (
          <EmptyCard
            title={tab === 'active' ? 'No alerts set' : 'No stopped alerts'}
            hint={
              tab === 'active'
                ? 'Set one with New, or from a symbol in the watchlist or the chart.'
                : 'An alert stops when it has fired once, reaches its end date or its contract expires.'
            }
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((row) => (
              <li key={row.id}>
                <AlertCard row={row} {...live(row)} engine={state.engine} onOpen={openAlert} />
              </li>
            ))}
          </ul>
        )}
        {followed.capped && (
          <p className="px-1 text-xs font-medium text-muted-foreground">
            Prices follow the first {LIVE_CAP} instruments on this tab.
          </p>
        )}
      </section>

      <AlertSheet
        row={overlay?.kind === 'alert' ? selected : null}
        live={selected ? live(selected) : NO_LIVE}
        onClose={close}
        onEdit={editAlert}
      />
      {overlay?.kind === 'new' && enabled && <AlertFormSheet onClose={close} onSaved={saved} />}
      {overlay?.kind === 'settings' && <SettingsSheet onClose={close} />}
      {overlay?.kind === 'edit' && selected && (
        <AlertFormSheet key={selected.id} row={selected} onClose={close} onSaved={saved} />
      )}
    </div>
  )
}

const NO_LIVE = {
  line: undefined,
  followed: false,
  feed: { connected: false, fallback: false, epoch: 0 },
  now: 0,
  marketOpen: null,
}

const TABS: Array<{ id: AlertTab; label: string }> = [
  { id: 'active', label: 'Active' },
  { id: 'stopped', label: 'Stopped' },
  { id: 'history', label: 'History' },
]

function Tabs({
  tab,
  counts,
  onSelect,
}: {
  tab: AlertTab
  counts: Record<'active' | 'stopped', number>
  onSelect: (tab: AlertTab) => void
}) {
  return (
    <div role="tablist" aria-label="Alerts" className="flex items-center gap-1.5">
      {TABS.map(({ id, label }) => {
        const selected = id === tab
        const count = id === 'history' ? null : counts[id]
        return (
          <button
            key={id}
            id={`sd-alerts-tab-${id}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls="sd-alerts-panel"
            onClick={() => onSelect(id)}
            className="group flex h-12 shrink-0 items-center outline-none"
          >
            <span
              className={cn(
                'flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                selected
                  ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                  : 'border-border bg-card font-semibold text-[color:var(--text-secondary)]'
              )}
            >
              {label}
              {count !== null && (
                <>
                  {' '}
                  <span className="text-xs font-semibold">({count})</span>
                </>
              )}
            </span>
          </button>
        )
      })}
    </div>
  )
}
