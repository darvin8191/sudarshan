import { LogOut } from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import type { MemberRow } from '@/sudarshan/screener/api'
import { exitedToday, tagsOf, usePin } from '@/sudarshan/screener/feed'
import type { ArrangeEdit } from './ArrangeSheet'
import { FOLLOW_CAP } from './follow'
import { figuresFor, type HeldFigures, HeldSwitch, indexBook, isinsOf } from './HoldingLine'
import { type ArrangeActions, type OrderRequest, SymbolSheet } from './SymbolSheet'
import { DEFAULT_PREF, isDefaultView } from './sort'
import { useHeld } from './useHeld'
import { type FollowOptions, useLiveRows } from './useLiveRows'
import { useSortOrder } from './useSortOrder'
import { ViewLine, type ViewPref, ViewSheet } from './ViewSheet'
import { marksOf } from './v2/marks'
import type { FlagName, View } from './v2/types'
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
  view,
  readOnly,
  flagNames,
  noRemove,
}: {
  /** v2 (F8): the five flag names; present while the switch is on. Rows carry flag and note markers and the sheet its flag and note. */
  flagNames?: readonly FlagName[]
  /** v2 (F8): a flag list is read-only: no Remove in the sheet. */
  noRemove?: boolean
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
  /** v2 (F7): the list's `view`, for each row's ISIN. */
  view?: View
  /** v2 (F7): a family render. It never shows the holdings line and never reads the books. */
  readOnly?: boolean
}) {
  const tags = useMemo(() => tagsOf(members ?? NO_MEMBERS), [members])
  const byKey = useMemo(
    () => new Map((members ?? NO_MEMBERS).map((row) => [keyOf(row.symbol, row.exchange), row])),
    [members]
  )
  const pref = viewPref?.pref // F6
  // F9 (v2): the cap and the window. The open sheet's instrument is followed first; a list longer than the
  // cap follows the window over the rows in the order they are shown, which a sort changes.
  const v2 = flagNames !== undefined
  const openKey = useMemo(() => {
    const open = openItemId === null ? undefined : items.find((item) => item.id === openItemId)
    return open ? keyOf(open.symbol, open.exchange) : null
  }, [items, openItemId])
  const [order, setOrder] = useState<string | null>(null)
  const listKey = viewPref?.ref ?? ''
  const follow = useMemo<FollowOptions | undefined>(
    () => (v2 ? { sheet: openKey ? [openKey] : [], order: order?.split(','), listKey } : undefined),
    [v2, openKey, order, listKey]
  )
  const live = useLiveRows({ items, apiKey, marketOpen: isOpen, basis: pref?.basis, follow })
  // F6: the rows in the snapshot's order; editing arranges the list's own order.
  const sorted = useSortOrder({
    rows: live.rows,
    groups: sections?.groups,
    pref: pref ?? DEFAULT_PREF,
    listKey: viewPref?.ref ?? '',
    ready: live.read,
    active: pref !== undefined && !isDefaultView(pref) && !editing,
    fullRead: live.readAll, // F9
  })
  const shown = editing ? live.rows : sorted.rows
  const shownOrder = v2 && items.length > FOLLOW_CAP ? shown.map((row) => row.key).join(',') : null
  useEffect(() => setOrder(shownOrder), [shownOrder])
  // F7: the holdings line, from the books, only when the switch is on and this is not a family render.
  const heldOn = !readOnly && pref?.held === true
  const book = useHeld({ enabled: heldOn, apiKey, marketOpen: isOpen })
  const isins = useMemo(() => isinsOf(view?.rows), [view])
  const held = useMemo(() => {
    if (!heldOn || !book) return undefined
    const index = indexBook(book)
    return new Map<string, HeldFigures>(
      items.map((item) => {
        const key = `${item.exchange.toUpperCase()}:${item.symbol.toUpperCase()}`
        return [key, figuresFor(index, item.exchange, item.symbol, isins.get(key))]
      })
    )
  }, [heldOn, book, items, isins])
  const saveHeld = useCallback(
    (next: boolean) => {
      if (pref) void viewPref?.save({ ...pref, held: next })
    },
    [pref, viewPref]
  )
  // F8: markers come from the view's rows; never in a family render (notes and flags are the owner's).
  const marks = useMemo(
    () => (flagNames && !readOnly ? marksOf(view?.rows, flagNames) : undefined),
    [flagNames, readOnly, view]
  )
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

      {pref && !editing && (
        <ViewLine pref={pref} at={sorted.at} readAt={sorted.readAt} onAgain={sorted.again} />
      )}

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
          held={held} // F7
          marks={marks} // F8
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
        onRemove={noRemove ? undefined : onRemove} // F8
        pin={pin}
        arrange={symbolActions} // F5
        flags={flagNames} // F8
        readOnly={readOnly} // F8
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
          extra={readOnly ? undefined : <HeldSwitch on={pref.held} onChange={saveHeld} />} // F7
        />
      )}
    </>
  )
})
