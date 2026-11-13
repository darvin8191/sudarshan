import { LogOut } from 'lucide-react'
import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { keyOf, type MarketOpen } from '@/sudarshan/dashboard/live'
import type { MemberRow } from '@/sudarshan/screener/api'
import { exitedToday, tagsOf, usePin } from '@/sudarshan/screener/feed'
import type { ArrangeEdit } from './ArrangeSheet'
import { FollowRowItem, followNames } from './FollowRows'
import { FOLLOW_CAP } from './follow'
import { type FollowForm, followsWords, underlyingKey } from './followRows'
import { figuresFor, type HeldFigures, HeldSwitch, indexBook, isinsOf } from './HoldingLine'
import { PresetLine } from './PresetLine'
import {
  type ArrangeActions,
  type FollowSheet,
  type OrderRequest,
  SymbolSheet,
} from './SymbolSheet'
import { DEFAULT_PREF, isDefaultView } from './sort'
import { SHEET_BASE, useFollowRows, useFollowTicks } from './useFollowRows'
import { useHeld } from './useHeld'
import { type FollowOptions, useLiveRows } from './useLiveRows'
import { useSortOrder } from './useSortOrder'
import { ViewLine, type ViewPref, ViewSheet } from './ViewSheet'
import { marksOf } from './v2/marks'
import type { FlagName, FollowRow, View } from './v2/types'
import { type FollowSlots, type SectionLayout, WatchlistRows } from './WatchlistRows'

/**
 * The part of the watchlist that moves with the market: the line over the
 * list, the rows and the open instrument's sheet. It holds the live data
 * itself, so a tick redraws this and nothing above it (the tabs, the header,
 * the dialogs), and it draws at most once a frame (`useFrameLines`); each row
 * redraws only when its own figures change.
 */
const NO_MEMBERS: MemberRow[] = []

/** F11: what the page gives the list for the rows that follow the price (the open own list's, v2 only). */
export interface FollowProps {
  rules: readonly FollowRow[]
  /** The row whose sheet is open, or null. */
  openRowId: number | null
  onOpenRow: (rowId: number) => void
  /** Open a form: add (from an underlying's sheet), change, or remove. */
  onForm: (form: FollowForm) => void
}

/** The contract a follow row's sheet opened with. */
interface Pin {
  rowId: number
  key: string
  symbol: string
  exchange: string
  names: ReturnType<typeof followNames>
}

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
  preset,
  followRows,
}: {
  /** v2 (F11): the open own list's rows that follow the price; absent for any other list and with the switch off. */
  followRows?: FollowProps
  /** v2 (F8): the five flag names; present while the switch is on. Rows carry flag and note markers and the sheet its flag and note. */
  flagNames?: readonly FlagName[]
  /** v2 (F10): a built-in list: the server's lines over the rows and Make a copy, which is given the keys as shown. */
  preset?: { lines: readonly string[]; onCopy: (keys: string[]) => void }
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
  // F11: the rows that follow the price. Each stands on a contract (an item of the list with a negative
  // id, so it is never an OpenAlgo item), kept current by `useFollowRows`; the open row's sheet stays
  // on the contract it opened with, which is followed too while it is open.
  const followOn = v2 && !readOnly && followRows !== undefined
  const fr = useFollowRows(followOn ? followRows.rules : undefined, followOn)
  const openRowId = followOn ? followRows.openRowId : null
  const current = openRowId === null ? undefined : fr.states.find((each) => each.id === openRowId)
  const [sheetPin, setSheetPin] = useState<Pin | null>(null)
  if (openRowId === null) {
    if (sheetPin !== null) setSheetPin(null)
  } else if (sheetPin?.rowId !== openRowId && current?.shown) {
    const { contract } = current.shown
    setSheetPin({
      rowId: openRowId,
      key: keyOf(contract.symbol, contract.exchange),
      symbol: contract.symbol,
      exchange: contract.exchange,
      names: followNames(current),
    })
  }
  const pinnedItem = useMemo<WatchlistItem | null>(() => {
    if (!sheetPin) return null
    return current?.item && keyOf(current.item.symbol, current.item.exchange) === sheetPin.key
      ? null // the row still stands on it: its own item serves
      : {
          id: -(SHEET_BASE + sheetPin.rowId),
          symbol: sheetPin.symbol,
          exchange: sheetPin.exchange,
          position: 0,
        }
  }, [sheetPin, current?.item])
  const followItems = useMemo(
    () => [
      ...fr.states.flatMap((each) => (each.item ? [each.item] : [])),
      ...(pinnedItem ? [pinnedItem] : []),
    ],
    [fr.states, pinnedItem]
  )
  const followKeys = followItems
    .map((each) => `${each.id}:${keyOf(each.symbol, each.exchange)}`)
    .join(',')
  // biome-ignore lint/correctness/useExhaustiveDependencies: followKeys is the identity of the follow items
  const liveItems = useMemo(
    () => (followItems.length > 0 ? [...followItems, ...items] : items),
    [followKeys, items]
  )
  const underlyings = useMemo(
    () => (followOn ? [...new Set(fr.states.map((each) => underlyingKey(each.rule)))] : undefined),
    [followOn, fr.states]
  )
  const openKey = useMemo(() => {
    if (sheetPin) return sheetPin.key
    const open = openItemId === null ? undefined : items.find((item) => item.id === openItemId)
    return open ? keyOf(open.symbol, open.exchange) : null
  }, [items, openItemId, sheetPin])
  const [order, setOrder] = useState<string | null>(null)
  const listKey = viewPref?.ref ?? ''
  const follow = useMemo<FollowOptions | undefined>(
    () =>
      v2
        ? { sheet: openKey ? [openKey] : [], underlyings, order: order?.split(','), listKey }
        : undefined,
    [v2, openKey, underlyings, order, listKey]
  )
  const live = useLiveRows({
    items: liveItems,
    apiKey,
    marketOpen: isOpen,
    basis: pref?.basis,
    follow,
  })
  useFollowTicks(fr.core, live.lines, live.feed, followOn)
  // The list's own rows: the follow rows' contracts are drawn by `FollowRowItem`, not sorted with them.
  const ownRows = useMemo(
    () => (followItems.length > 0 ? live.rows.filter((row) => row.item.id > 0) : live.rows),
    [live.rows, followItems.length]
  )
  // F6: the rows in the snapshot's order; editing arranges the list's own order.
  const sorted = useSortOrder({
    rows: ownRows,
    groups: sections?.groups,
    pref: pref ?? DEFAULT_PREF,
    listKey: viewPref?.ref ?? '',
    ready: live.read,
    active: pref !== undefined && !isDefaultView(pref) && !editing,
    fullRead: live.readAll, // F9
  })
  const shown = editing ? ownRows : sorted.rows
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
  const sheetRow = sheetPin
    ? (live.rows.find((row) => row.key === sheetPin.key) ?? null) // F11: the contract it opened with
    : openItemId === null
      ? null
      : (live.rows.find((row) => row.item.id === openItemId) ?? null)
  const pin = usePin(sheetRow ? byKey.get(sheetRow.key) : undefined)
  const exited = exitedToday(members ?? NO_MEMBERS, live.now)
  const nothingShown = ownRows.length > 0 && ownRows.every((row) => row.view.ltp === null)
  const readFailed = apiKey !== null && live.snapshot === 'error' && ownRows.length > 0

  // F11: the rows that follow the price, by section; a row with no contract opens its form.
  const liveById = useMemo(() => new Map(live.rows.map((row) => [row.item.id, row])), [live.rows])
  const onOpenRow = followRows?.onOpenRow
  const onForm = followRows?.onForm
  const slots = useMemo<FollowSlots | undefined>(
    () =>
      followOn
        ? {
            count: fr.states.length,
            render: (sectionId) =>
              fr.states
                .filter((each) => (each.rule.section_id ?? null) === sectionId)
                .map((each) => (
                  <FollowRowItem
                    key={`follow-${each.id}`}
                    state={each}
                    live={liveById.get(-each.id) ?? null}
                    now={live.now}
                    onOpen={(rowId) =>
                      each.item ? onOpenRow?.(rowId) : onForm?.({ mode: 'edit', rowId })
                    }
                  />
                )),
          }
        : undefined,
    [followOn, fr.states, liveById, live.now, onOpenRow, onForm]
  )
  // A row's sheet that has no contract to show (it went missing meanwhile) opens the row's form instead.
  const pending = openRowId !== null && current !== undefined && !current.shown && !sheetPin
  useEffect(() => {
    if (pending && openRowId !== null) onForm?.({ mode: 'edit', rowId: openRowId })
  }, [pending, openRowId, onForm])
  const followSheet = useMemo<FollowSheet | undefined>(() => {
    if (!sheetPin || !current || openRowId === null) return undefined
    const moved =
      current.shown &&
      keyOf(current.shown.contract.symbol, current.shown.contract.exchange) !== sheetPin.key
    return {
      ...sheetPin.names,
      moved: moved
        ? { words: followsWords(current.rule, current.shown), onShow: () => setSheetPin(null) }
        : null,
      onEdit: () => onForm?.({ mode: 'edit', rowId: openRowId }),
      onRemove: () => onForm?.({ mode: 'remove', rowId: openRowId }),
    }
  }, [sheetPin, current, openRowId, onForm])
  const offer = useMemo(
    () =>
      followOn && symbolActions
        ? {
            onAdd: (kind: 'atm_option' | 'month_future', item: WatchlistItem) =>
              onForm?.({
                mode: 'add',
                kind,
                underlying: item.symbol,
                exchange: item.exchange,
                sectionId: view?.rows.find((row) => row.item === item.id)?.section_id ?? null,
              }),
          }
        : undefined,
    [followOn, symbolActions, onForm, view]
  )

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

      {preset && (
        <PresetLine // F10
          lines={preset.lines}
          empty={shown.length === 0}
          onCopy={() => preset.onCopy(shown.map((row) => row.key))}
        />
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
          follow={slots} // F11
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
        follow={followSheet} // F11
        offer={offer} // F11
      />

      {viewPref && pref && (
        <ViewSheet
          open={viewOpen === true}
          onClose={onCloseSheet}
          pref={pref}
          onChange={(next) => void viewPref.save(next)}
          problem={viewPref.problem}
          noOpen={ownRows.filter((row) => row.view.openMissing).length}
          total={ownRows.length}
          extra={readOnly ? undefined : <HeldSwitch on={pref.held} onChange={saveHeld} />} // F7
        />
      )}
    </>
  )
})
