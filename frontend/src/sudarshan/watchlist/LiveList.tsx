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
import {
  dayLabel,
  derivativesExchange,
  type FollowForm,
  followsWords,
  lineOf,
  num,
  underlyingKey,
} from './followRows'
import { figuresFor, type HeldFigures, HeldSwitch, indexBook, isinsOf } from './HoldingLine'
import { PresetLine } from './PresetLine'
import { chainOf } from './rows'
import { StrikePicker } from './StrikePicker' // F14
import { StrikeRowItem, strikeNames } from './StrikeRow'
import { StrikeSheet } from './StrikeSheet'
import {
  type ArrangeActions,
  type FollowSheet,
  type OrderRequest,
  SymbolSheet,
} from './SymbolSheet'
import { DEFAULT_PREF, isDefaultView } from './sort'
import { PICKER_ITEM } from './strikePicker' // F14
import {
  EXTRA_BASE,
  PE_BASE,
  peItemIdOf,
  SHEET_BASE,
  useFollowRows,
  useFollowTicks,
} from './useFollowRows'
import { useHeld } from './useHeld'
import { type FollowOptions, useLiveRows } from './useLiveRows'
import { useSameStrike } from './useSameStrike'
import { useSortOrder } from './useSortOrder'
import { useStrikeLadder } from './useStrikeLadder' // F14
import { ViewLine, type ViewPref, ViewSheet } from './ViewSheet'
import { marksOf } from './v2/marks'
import type { Contract, FlagName, FollowRow, View } from './v2/types'
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
  /** F12: a strike row's two legs, the strike and the expiry the sheet opened with. */
  pair?: { ce: Contract; pe: Contract; strike: number | null }
}

/** F14: the strike picker the list holds open (v2 only): its underlying's price and the strikes' prices tick while it is. */
export interface PickerProps {
  underlying: string
  exchange: string
  listName: string
  full: boolean
  onAdd: (symbol: string, exchange: string) => Promise<void>
  onFollow: (form: FollowForm) => void
  onClose: () => void
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
  picker,
}: {
  /** v2 (F14): the strike picker, open or absent. */
  picker?: PickerProps
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
    const { contract, pair } = current.shown
    setSheetPin({
      ...(pair ? { pair: { ...pair, strike: current.shown.strike } } : {}),
      rowId: openRowId,
      key: keyOf(contract.symbol, contract.exchange),
      symbol: contract.symbol,
      exchange: contract.exchange,
      names: followNames(current),
    })
  }
  const pinnedItems = useMemo<WatchlistItem[]>(() => {
    if (!sheetPin) return []
    const at = (id: number, c: { symbol: string; exchange: string }): WatchlistItem => ({
      id,
      symbol: c.symbol,
      exchange: c.exchange,
      position: 0,
    })
    const stands = (item: WatchlistItem | null | undefined, c: Contract) =>
      item !== null && item !== undefined && keyOf(item.symbol, item.exchange) === c.key
    if (sheetPin.pair) {
      const { ce, pe } = sheetPin.pair // F12
      return stands(current?.item, ce) && stands(current?.peItem, pe)
        ? [] // the row still stands on them: its own items serve
        : [at(-(SHEET_BASE + sheetPin.rowId), ce), at(-(SHEET_BASE + PE_BASE + sheetPin.rowId), pe)]
    }
    return current?.item && keyOf(current.item.symbol, current.item.exchange) === sheetPin.key
      ? [] // the row still stands on it: its own item serves
      : [at(-(SHEET_BASE + sheetPin.rowId), sheetPin)]
  }, [sheetPin, current?.item, current?.peItem])
  // F12: the same strike on the next expiries, followed (first in the cap, Quote mode) only while the sheet is open.
  const same = useSameStrike(
    sheetPin?.pair && sheetPin.pair.strike !== null && sheetPin.pair.ce.expiry && current
      ? {
          underlying: current.rule.underlying,
          exchange: current.rule.underlying_exchange, // F13: the `strikes` op takes the underlying's own
          strike: sheetPin.pair.strike,
          after: sheetPin.pair.ce.expiry,
        }
      : null
  )
  const extraItems = useMemo<WatchlistItem[]>(
    () =>
      same.rows.flatMap((each, index) =>
        [each.ce, each.pe].flatMap((c, side) =>
          c
            ? [
                {
                  id: -(EXTRA_BASE + index * 2 + side),
                  symbol: c.symbol,
                  exchange: c.exchange,
                  position: 0,
                },
              ]
            : []
        )
      ),
    [same.rows]
  )
  // F14: the picker's ladder; the underlying is read like a row, and the strikes' CE and PE are followed
  // (first in the cap) only while the picker is open and this tab's strikes are the ones on screen.
  const pickOn = v2 && !readOnly && picker !== undefined
  const ladder = useStrikeLadder(
    pickOn ? { underlying: picker.underlying, exchange: picker.exchange } : null
  )
  const pickKey = pickOn ? keyOf(picker.underlying, picker.exchange) : null
  const pickItems = useMemo<WatchlistItem[]>(
    () =>
      pickOn
        ? [{ id: PICKER_ITEM, symbol: picker.underlying, exchange: picker.exchange, position: 0 }]
        : [],
    [pickOn, picker?.underlying, picker?.exchange]
  )
  const pickKeys = useMemo(
    () => [
      ...(pickKey ? [pickKey] : []),
      ...(ladder.data?.strikes ?? [])
        .flatMap((each) => [each.ce?.key, each.pe?.key])
        .flatMap((key) => (key ? [key] : [])),
    ],
    [pickKey, ladder.data]
  )
  const followItems = useMemo(
    () => [
      ...fr.states.flatMap((each) => [
        ...(each.item ? [each.item] : []),
        ...(each.peItem ? [each.peItem] : []), // F12: a strike row's PE
      ]),
      ...pinnedItems,
      ...extraItems, // F12
      ...pickItems, // F14
    ],
    [fr.states, pinnedItems, extraItems, pickItems]
  )
  // F12: a strike row counts 2 in the cap (its CE and its PE) and follows both in Quote mode (open interest).
  const strikeKeys = useMemo(() => {
    const map = new Map<string, readonly string[]>()
    for (const each of fr.states)
      if (each.item && each.peItem)
        map.set(keyOf(each.item.symbol, each.item.exchange), [
          keyOf(each.item.symbol, each.item.exchange),
          keyOf(each.peItem.symbol, each.peItem.exchange),
        ])
    return map
  }, [fr.states])
  const quoteKeys = useMemo(() => [...strikeKeys.values()].flat(), [strikeKeys])
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
  const openKeys = useMemo(() => {
    if (sheetPin) {
      // F12: a strike sheet follows both legs and the other expiries, all counted first in the cap
      const legs = sheetPin.pair ? [sheetPin.pair.ce.key, sheetPin.pair.pe.key] : [sheetPin.key]
      return [...legs, ...extraItems.map((each) => keyOf(each.symbol, each.exchange))]
    }
    if (pickKeys.length > 0) return pickKeys // F14: the picker is the open sheet
    const open = openItemId === null ? undefined : items.find((item) => item.id === openItemId)
    return open ? [keyOf(open.symbol, open.exchange)] : []
  }, [items, openItemId, sheetPin, extraItems, pickKeys])
  const [order, setOrder] = useState<string | null>(null)
  const listKey = viewPref?.ref ?? ''
  const follow = useMemo<FollowOptions | undefined>(
    () =>
      v2
        ? {
            sheet: openKeys,
            underlyings,
            order: order?.split(','),
            listKey,
            strikes: strikeKeys, // F12
            quote: sheetPin?.pair ? [...quoteKeys, ...openKeys] : quoteKeys, // F12
          }
        : undefined,
    [v2, openKeys, underlyings, order, listKey, strikeKeys, quoteKeys, sheetPin?.pair]
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
  const heldKeys = useMemo(
    () => new Set(items.map((item) => keyOf(item.symbol, item.exchange))),
    [items]
  ) // F14
  const sheetRow = sheetPin?.pair
    ? null // F12: a strike row's sheet is the strike sheet
    : sheetPin
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
                .map((each) =>
                  each.rule.kind === 'strike_pair' ? ( // F12
                    <StrikeRowItem
                      key={`follow-${each.id}`}
                      state={each}
                      ce={liveById.get(-each.id) ?? null}
                      pe={liveById.get(peItemIdOf(each.id)) ?? null}
                      now={live.now}
                      onOpen={(rowId) => onOpenRow?.(rowId)}
                    />
                  ) : (
                    <FollowRowItem
                      key={`follow-${each.id}`}
                      state={each}
                      live={liveById.get(-each.id) ?? null}
                      now={live.now}
                      onOpen={(rowId) =>
                        each.item ? onOpenRow?.(rowId) : onForm?.({ mode: 'edit', rowId })
                      }
                    />
                  )
                ),
          }
        : undefined,
    [followOn, fr.states, liveById, live.now, onOpenRow, onForm]
  )
  // A row's sheet that has no contract to show (it went missing meanwhile) opens the row's form instead.
  const isPair = current?.rule.kind === 'strike_pair' // F12: a pair with no contract says why in its sheet
  const pending =
    openRowId !== null && current !== undefined && !current.shown && !sheetPin && !isPair
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
  // F12: the sheet of a strike row, on the legs it opened with.
  const liveByKey = useMemo(() => new Map(live.rows.map((row) => [row.key, row])), [live.rows])
  // F14: the underlying's price as it moves tells the ladder whether the ATM has moved to a neighbour.
  const pickPrice = pickKey ? (liveByKey.get(pickKey)?.view.ltp ?? null) : null
  const observe = ladder.observe
  const ladderData = ladder.data
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new answer is a new moment to compare the price with it
  useEffect(() => observe(pickPrice), [observe, pickPrice, ladderData])
  const names = current ? strikeNames(current) : null
  const strikeSheet = (() => {
    if (!current || !isPair || openRowId === null || !names) return null
    const pair = sheetPin?.pair
    const now = current.shown?.pair
    const moved =
      pair && now && (now.ce.key !== pair.ce.key || now.pe.key !== pair.pe.key)
        ? { words: followsWords(current.rule, current.shown), onShow: () => setSheetPin(null) }
        : null
    const chain = chainOf(current.rule.underlying, current.rule.underlying_exchange)
    return (
      <StrikeSheet
        title={`${current.rule.underlying} ${pair?.strike === null || !pair ? names.middle : num(pair.strike)}`}
        detail={`${derivativesExchange(current.rule)}${pair?.ce.expiry ? ` · ${dayLabel(pair.ce.expiry)}` : ''} · follows ${current.rule.underlying}`}
        chip={names.chip}
        help={names.help}
        words={lineOf(current, live.now)}
        moved={moved}
        strike={pair?.strike ?? null}
        expiry={pair?.ce.expiry ?? null}
        legs={
          pair
            ? {
                ce: { contract: pair.ce, live: liveByKey.get(pair.ce.key) ?? null },
                pe: { contract: pair.pe, live: liveByKey.get(pair.pe.key) ?? null },
              }
            : null
        }
        lines={live.lines}
        others={{
          state: same.state,
          retry: same.retry,
          rows: same.rows.map((each) => ({
            expiry: each.expiry,
            ce: each.ce ? (liveByKey.get(each.ce.key) ?? null) : null,
            pe: each.pe ? (liveByKey.get(each.pe.key) ?? null) : null,
          })),
        }}
        chain={chain}
        feed={live.feed}
        now={live.now}
        marketOpen={isOpen('NFO')}
        onClose={onCloseSheet}
        onOrder={onOrder}
        onEdit={() => onForm?.({ mode: 'edit', rowId: openRowId })}
        onRemove={() => onForm?.({ mode: 'remove', rowId: openRowId })}
      />
    )
  })()
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

      {strikeSheet}

      {pickOn && (
        <StrikePicker // F14
          underlying={picker.underlying}
          exchange={picker.exchange}
          ladder={ladder}
          under={pickKey ? (liveByKey.get(pickKey) ?? null) : null}
          lines={live.lines}
          feed={live.feed}
          now={live.now}
          marketOpen={isOpen(
            ladderData?.strikes.find((each) => each.ce ?? each.pe)?.ce?.exchange ?? 'NFO'
          )}
          held={heldKeys}
          full={picker.full}
          listName={picker.listName}
          onAdd={picker.onAdd}
          onFollow={picker.onFollow}
          chain={chainOf(picker.underlying, picker.exchange)}
          onClose={picker.onClose}
        />
      )}

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
