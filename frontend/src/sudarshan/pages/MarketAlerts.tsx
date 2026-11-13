// SUDARSHAN M10.3: the Alerts page, phone first. The owner's market alerts as the alert service
// holds them (blueprints/sudarshan_alerts.py, contract 1.1), kept current by what it pushes
// (src/sudarshan/alerts/marketAlerts.ts), with each card's price from the tab's own tick stream.
// At /market-alerts because upstream owns /alerts, for the /trading chart's own alerts.
import { AlertTriangle, LoaderCircle, Plus, RefreshCw, Settings2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { AlertRow, GroupRow } from '@/api/marketAlerts'
import { cn } from '@/lib/utils'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import { AlertCard } from '@/sudarshan/alerts/AlertCard'
import { AlertFormSheet } from '@/sudarshan/alerts/AlertForm'
import { AlertSheet } from '@/sudarshan/alerts/AlertSheet'
import { droppedHere } from '@/sudarshan/alerts/actions'
import { GroupCard } from '@/sudarshan/alerts/GroupCard'
import { HistoryList } from '@/sudarshan/alerts/HistoryList'
import { ListAlertFormSheet } from '@/sudarshan/alerts/ListAlertForm'
import { LineText } from '@/sudarshan/alerts/lines'
import { needsQuote, segmentOpen } from '@/sudarshan/alerts/measure'
import { SettingsSheet } from '@/sudarshan/alerts/SettingsSheet'
import { type AlertTab, useAlertOverlay } from '@/sudarshan/alerts/useAlertOverlay'
import { useAlertsHearing, useMarketAlertsState } from '@/sudarshan/alerts/useMarketAlertsState'
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
import { toast } from '@/utils/toast'

/** The most instruments the page follows tick by tick (cross-design R4). */
export const LIVE_CAP = 100

const NO_ROWS: AlertRow[] = []
const NO_GROUPS: GroupRow[] = []

/** In place of the engine line while this screen is not in the owner's room. */
const NOT_RECEIVING = {
  code: 'feed_down',
  line: 'Not receiving alerts on this screen · reconnecting',
  real_time: false,
} as const

/** An alert whose sheet or form is open went on another screen. */
const GONE = 'This alert was deleted.'
const GROUP_GONE = 'This list alert was deleted.'

const HEADER_BUTTON =
  'inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:bg-muted disabled:text-[color:var(--text-tertiary)] disabled:shadow-[inset_0_0_0_1px_var(--border-strong)]'

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

/** The list alerts' cards by tab, ahead of the ordinary alerts: the same order of statuses. */
function groupTabsOf(groups: GroupRow[]): Record<'active' | 'stopped', GroupRow[]> {
  const byNewest = (a: GroupRow, b: GroupRow) => b.id - a.id
  const of = (status: GroupRow['status']) =>
    groups.filter((g) => g.status === status).sort(byNewest)
  return {
    active: [...of('active'), ...of('paused')],
    stopped: of('stopped').sort((a, b) => b.updated_at - a.updated_at || byNewest(a, b)),
  }
}

const bySymbol = (a: AlertRow, b: AlertRow) => a.symbol.localeCompare(b.symbol) || a.id - b.id

interface Followed {
  /** Instruments whose alerts need the bid, ask, open, high, low or VWAP: the Quote stream. */
  quote: Instrument[]
  /** The rest, on the lighter LTP stream. */
  ltp: Instrument[]
  keys: Set<string>
  /** Some cards' instruments are left out, over the cap. */
  capped: boolean
}

/**
 * The instruments the page follows, each once, in the order given, up to `cap`; each on the
 * Quote stream only when one of its alerts needs a figure the LTP stream does not carry.
 */
function followedOf(rows: AlertRow[], cap: number): Followed {
  const byKey = new Map<string, { instrument: Instrument; quote: boolean }>()
  let capped = false
  for (const row of rows) {
    const key = keyOf(row.symbol, row.exchange)
    const held = byKey.get(key)
    if (held) {
      held.quote ||= needsQuote(row)
      continue
    }
    if (byKey.size >= cap) {
      capped = true
      continue
    }
    byKey.set(key, {
      instrument: { symbol: row.symbol, exchange: row.exchange },
      quote: needsQuote(row),
    })
  }
  const quote: Instrument[] = []
  const ltp: Instrument[] = []
  for (const each of byKey.values()) (each.quote ? quote : ltp).push(each.instrument)
  return { quote, ltp, keys: new Set(byKey.keys()), capped }
}

export default function MarketAlerts() {
  const query = useMarketAlertsState()
  const state = query.data
  const access = usePriceAccess()
  const ready = access.state === 'ready'
  const { overlay, tab, open, close, setTab } = useAlertOverlay()
  const now = useNow()

  const alerts = state?.alerts ?? NO_ROWS
  // Member alerts of a list alert are not cards of their own: they show inside its card.
  const loose = useMemo(() => alerts.filter((row) => row.group_id == null), [alerts])
  const tabs = useMemo(() => tabsOf(loose), [loose])
  const groupTabs = useMemo(() => groupTabsOf(state?.groups ?? NO_GROUPS), [state?.groups])
  const groupCards = tab === 'history' ? NO_GROUPS : groupTabs[tab]
  const rows = tab === 'history' ? NO_ROWS : tabs[tab]
  // The cards the owner has opened, whose member alerts show and have their prices followed.
  const [opened, setOpened] = useState<ReadonlySet<number>>(new Set())
  const toggleGroup = useCallback(
    (id: number) =>
      setOpened((now) => {
        const next = new Set(now)
        if (!next.delete(id)) next.add(id)
        return next
      }),
    []
  )
  // A7: "Open the list alert" on a member's sheet closes it and opens that group's card, on the
  // tab the card is on (the tab changes once the sheet has closed, so the two do not collide).
  const [showTab, setShowTab] = useState<AlertTab | null>(null)
  const openGroup = useCallback(
    (id: number) => {
      const found = (state?.groups ?? NO_GROUPS).find((g) => g.id === id)
      setOpened((now) => new Set(now).add(id))
      if (found) setShowTab(found.status === 'stopped' ? 'stopped' : 'active')
      close()
    },
    [state?.groups, close]
  )
  useEffect(() => {
    if (showTab === null || overlay !== null) return
    if (showTab !== tab) setTab(showTab)
    setShowTab(null)
  }, [showTab, overlay, tab, setTab])
  const membersOf = useMemo(() => {
    const out = new Map<number, AlertRow[]>()
    for (const row of alerts) {
      if (row.group_id == null) continue
      out.set(row.group_id, [...(out.get(row.group_id) ?? []), row])
    }
    for (const list of out.values()) list.sort(bySymbol)
    return out
  }, [alerts])
  const shownMembers = useMemo(
    () => groupCards.filter((g) => opened.has(g.id)).flatMap((g) => membersOf.get(g.id) ?? []),
    [groupCards, opened, membersOf]
  )
  const shownId = overlay?.kind === 'alert' || overlay?.kind === 'edit' ? overlay.id : null
  const selected = shownId === null ? null : (alerts.find((row) => row.id === shownId) ?? null)

  // What the page follows (cross-design R4): the open sheet's alert first, which a link can open
  // from another tab, then the tab's cards in order. 100 instruments in all: an open form follows
  // its own instrument, so the page keeps one place free for it.
  const formOpen = overlay?.kind === 'new' || overlay?.kind === 'edit'
  const cap = formOpen ? LIVE_CAP - 1 : LIVE_CAP
  const followed = useMemo(
    () =>
      followedOf(selected ? [selected, ...rows, ...shownMembers] : [...rows, ...shownMembers], cap),
    [selected, rows, shownMembers, cap]
  )
  const membersCut = shownMembers.filter(
    (row) => !followed.keys.has(keyOf(row.symbol, row.exchange))
  ).length
  const quoted = useFrameLines({
    instruments: followed.quote,
    mode: 'Quote',
    enabled: ready,
  })
  const plain = useFrameLines({ instruments: followed.ltp, mode: 'LTP', enabled: ready })
  const lines = useMemo(
    () => new Map([...plain.lines, ...quoted.lines]),
    [plain.lines, quoted.lines]
  )
  // One manager behind both streams: one feed.
  const feed = plain.feed

  // An overlay that cannot be shown (an alert deleted meanwhile, a new alert with market alerts
  // off) is closed once the alerts are known, rather than left open over nothing. An alert that
  // went on another screen says so; one deleted here has said so already.
  const enabled = state?.enabled === true
  // A list alert (A6): its form needs market alerts on; editing one needs the group.
  const groupId = overlay?.kind === 'group' ? overlay.id : null
  const shownGroup =
    groupId === null ? null : ((state?.groups ?? []).find((each) => each.id === groupId) ?? null)
  const orphan =
    overlay !== null &&
    state !== undefined &&
    (overlay.kind === 'new' || overlay.kind === 'list'
      ? !enabled
      : overlay.kind === 'group'
        ? shownGroup === null
        : overlay.kind !== 'settings' && selected === null)
  useEffect(() => {
    if (!orphan) return
    if (groupId !== null) toast.info(GROUP_GONE)
    else if (shownId !== null && !droppedHere(shownId)) toast.info(GONE)
    close()
  }, [orphan, close, shownId, groupId])

  const openAlert = useCallback((id: number) => open({ kind: 'alert', id }), [open])
  const editAlert = useCallback((id: number) => open({ kind: 'edit', id }), [open])
  const editGroup = useCallback((id: number) => open({ kind: 'group', id }), [open])
  // Saved: the alert's own sheet takes the form's place, so the owner sees what was saved.
  const saved = useCallback((row: AlertRow) => open({ kind: 'alert', id: row.id }), [open])
  const live = (row: AlertRow) => ({
    line: lines.get(keyOf(row.symbol, row.exchange)),
    followed: ready && followed.keys.has(keyOf(row.symbol, row.exchange)),
    feed,
    now,
    marketOpen: segmentOpen(state?.segments, row),
  })
  const problem = query.error
  // The engine line is as current as the page can hear. Not in the owner's room (the connection
  // is down, or the join has no answer yet), no push reaches this screen: it says so in place of
  // the line. With the page's own feed down its cards say Reconnecting, so the line stays, grey,
  // "as last read". Either way it never claims Real-time.
  const hearing = useAlertsHearing()
  const deaf = followed.keys.size > 0 && ready && feed.epoch > 0 && !feed.connected

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
        ) : state && hearing === 'not_joined' ? (
          <span data-heard="no" className="inline-flex">
            <LineText line={NOT_RECEIVING} />
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

      <AccessNotice access={access} />

      <Tabs
        tab={tab}
        counts={{
          active: tabs.active.length + groupTabs.active.length,
          stopped: tabs.stopped.length + groupTabs.stopped.length,
        }}
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
        ) : rows.length + groupCards.length === 0 ? (
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
            {groupCards.map((group) => (
              <li key={`group-${group.id}`}>
                <GroupCard
                  group={group}
                  members={membersOf.get(group.id) ?? NO_ROWS}
                  expanded={opened.has(group.id)}
                  onToggle={toggleGroup}
                  onEdit={editGroup}
                  renderMember={(row) => (
                    <AlertCard row={row} {...live(row)} engine={state.engine} onOpen={openAlert} />
                  )}
                />
              </li>
            ))}
            {rows.map((row) => (
              <li key={row.id}>
                <AlertCard row={row} {...live(row)} engine={state.engine} onOpen={openAlert} />
              </li>
            ))}
          </ul>
        )}
        {followed.capped && (
          <p className="px-1 text-xs font-medium text-muted-foreground">
            Prices follow the first {cap} instruments on this tab.
            {membersCut > 0 &&
              ` ${membersCut} ${membersCut === 1 ? 'symbol' : 'symbols'} of the list alerts you opened ${membersCut === 1 ? 'shows' : 'show'} no price.`}
          </p>
        )}
      </section>

      <AlertSheet
        row={overlay?.kind === 'alert' ? selected : null}
        live={selected ? live(selected) : NO_LIVE}
        onClose={close}
        onEdit={editAlert}
        group={
          selected?.group_id == null
            ? null
            : ((state?.groups ?? NO_GROUPS).find((g) => g.id === selected.group_id) ?? null)
        }
        onOpenGroup={openGroup}
      />
      {overlay?.kind === 'new' && enabled && <AlertFormSheet onClose={close} onSaved={saved} />}
      {overlay?.kind === 'list' && enabled && (
        <ListAlertFormSheet
          key={`${overlay.ref}`}
          target={{ ref: overlay.ref, name: overlay.name, count: overlay.count, fed: overlay.fed }}
          onClose={close}
        />
      )}
      {overlay?.kind === 'group' && shownGroup && (
        <ListAlertFormSheet key={shownGroup.id} group={shownGroup} onClose={close} />
      )}
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
