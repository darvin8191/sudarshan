import { LogOut } from 'lucide-react'
import { memo, useMemo } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import type { MemberRow } from '@/sudarshan/screener/api'
import { exitedToday, tagsOf, usePin } from '@/sudarshan/screener/feed'
import type { ArrangeEdit } from './ArrangeSheet'
import { type ArrangeActions, type OrderRequest, SymbolSheet } from './SymbolSheet'
import { DEFAULT_PREF, isDefaultView } from './sort'
import { useLiveRows } from './useLiveRows'
import { useSortOrder } from './useSortOrder'
import { ViewLine, type ViewPref, ViewSheet } from './ViewSheet'
import { type SectionLayout, WatchlistRows } from './WatchlistRows'

/**
 * The part of the watchlist that moves with the market: the line over the
 * list, the rows and the open instrument's sheet. It holds the live data
 * itself, so a tick redraws this and nothing above it (the tabs, the header,
 * the dialogs), and it draws at most once a frame (`useFrameLines`); each row
 * redraws only when its own figures change.
 */
const NO_MEMBERS: MemberRow[] = []

export const LiveList = memo(function LiveList({
  items,
  apiKey,
  isOpen,
  loading,
  editing,
  listName,
  openItemId,
  panel,
  onOpen,
  onMove,
  onRemove,
  onAdd,
  onCloseSheet,
  onOrder,
  onAlert,
  members,
  sections,
  arrange,
  symbolActions,
  viewPref,
  viewOpen,
}: {
  items: WatchlistItem[]
  apiKey: string | null
  isOpen: MarketOpen
  loading: boolean
  editing: boolean
  listName: string
  /** The item whose sheet is open, or null. */
  openItemId: number | null
  /** How the list's section names itself: the open tab's panel, or a plain region. */
  panel: { id?: string; role?: 'tabpanel'; 'aria-labelledby'?: string; 'aria-label'?: string }
  onOpen: (itemId: number) => void
  onMove: (item: WatchlistItem, by: -1 | 1) => void
  onRemove: (item: WatchlistItem) => void
  /** Absent while there is no API key to search with: the page says why. */
  onAdd?: () => void
  onCloseSheet: () => void
  onOrder: (order: OrderRequest) => void
  onAlert: (alert: AlertPrefill) => void
  /** What a screener put in this list (M9 UI7): chips on rows, who left today, Pin in the sheet. */
  members?: readonly MemberRow[]
  /** v2 (F3): the open own list's sections; absent with the switch off. */
  sections?: SectionLayout
  /** v2 (F5): edit mode as a draft, and what the symbol sheet adds. */
  arrange?: ArrangeEdit
  symbolActions?: ArrangeActions
  /** v2 (F6): the list's saved View and its sheet; absent with the switch off, and for a list it does not apply to. */
  viewPref?: ViewPref
  viewOpen?: boolean
}) {
  const tags = useMemo(() => tagsOf(members ?? NO_MEMBERS), [members])
  const byKey = useMemo(
    () => new Map((members ?? NO_MEMBERS).map((row) => [keyOf(row.symbol, row.exchange), row])),
    [members]
  )
  const pref = viewPref?.pref // F6
  const live = useLiveRows({ items, apiKey, marketOpen: isOpen, basis: pref?.basis })
  // F6: the rows in the snapshot's order; editing arranges the list's own order.
  const sorted = useSortOrder({
    rows: live.rows,
    groups: sections?.groups,
    pref: pref ?? DEFAULT_PREF,
    listKey: viewPref?.ref ?? '',
    ready: live.read,
    active: pref !== undefined && !isDefaultView(pref) && !editing,
  })
  const shown = editing ? live.rows : sorted.rows
  const sheetRow =
    openItemId === null ? null : (live.rows.find((row) => row.item.id === openItemId) ?? null)
  const pin = usePin(sheetRow ? byKey.get(sheetRow.key) : undefined)
  const exited = exitedToday(members ?? NO_MEMBERS, live.now)
  const nothingShown = live.rows.length > 0 && live.rows.every((row) => row.view.ltp === null)
  const readFailed = apiKey !== null && live.snapshot === 'error' && live.rows.length > 0

  return (
    <>
      {/* No key to read with: nothing is connecting, and the page's notice says why. */}
      {apiKey !== null && live.line && (
        <LiveLine line={live.line} now={live.now} className="flex min-h-5 px-1" />
      )}

      {readFailed && (
        <output className="flex flex-wrap items-center gap-x-2 px-1 text-xs font-medium text-muted-foreground">
          {nothingShown
            ? 'Prices are not available right now.'
            : 'Prices could not be refreshed just now.'}
          <button
            type="button"
            onClick={live.retry}
            className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
          >
            Try again
          </button>
        </output>
      )}

      {pref && !editing && <ViewLine pref={pref} at={sorted.at} onAgain={sorted.again} />}

      <section
        {...panel}
        className="overflow-hidden rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]"
      >
        <WatchlistRows
          rows={shown}
          loading={loading}
          editing={editing}
          onOpen={onOpen}
          onMove={onMove}
          onRemove={onRemove}
          onAdd={onAdd}
          tags={tags}
          sections={sections && { ...sections, groups: sorted.groups ?? sections.groups }} // F6
          arrange={arrange} // F5
        />
      </section>
      {exited.length > 0 && (
        <p
          data-exited-today="true"
          className="flex items-start gap-1.5 px-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]"
        >
          <LogOut aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          Exited today ({exited.length}): {exited.join(' · ')}
        </p>
      )}

      <SymbolSheet
        row={sheetRow}
        listName={listName}
        apiKey={apiKey}
        marketOpen={sheetRow ? isOpen(sheetRow.item.exchange) : null}
        feed={live.feed}
        now={live.now}
        onClose={onCloseSheet}
        onOrder={onOrder}
        onAlert={onAlert}
        onRemove={onRemove}
        pin={pin}
        arrange={symbolActions} // F5
      />

      {viewPref && pref && (
        <ViewSheet
          open={viewOpen === true}
          onClose={onCloseSheet}
          pref={pref}
          onChange={(next) => void viewPref.save(next)}
          problem={viewPref.problem}
          noOpen={live.rows.filter((row) => row.view.openMissing).length}
          total={live.rows.length}
        />
      )}
    </>
  )
})
