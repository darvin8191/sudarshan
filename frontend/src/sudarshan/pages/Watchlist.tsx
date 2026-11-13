// SUDARSHAN (M8.1, M8.2, M8.3): the phone-first watchlist, the first screen on
// the bottom bar as in a broker's app. The lists are the server-side ones the
// /trading terminal's panel uses (blueprints/watchlist.py), the prices stream
// tick by tick on the tab's shared market-data socket, and a tap opens the
// instrument's sheet: Buy and Sell, chart, option chain and depth.

import { useQueryClient } from '@tanstack/react-query'
import { Check, Plus, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import type { WatchlistItem } from '@/api/watchlist'
import { useSocketContext } from '@/components/socket/SocketProvider'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { AccessNotice } from '@/sudarshan/AccessNotice'
import { usePriceAccess } from '@/sudarshan/access'
import { AlertFormSheet, type AlertPrefill, toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { keyOf } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { useFedLists, useListMembership } from '@/sudarshan/screener/feed'
import { useScreenerPush } from '@/sudarshan/screener/push'
import { AddSymbolSheet } from '@/sudarshan/watchlist/AddSymbolSheet'
import { AllListsSheet } from '@/sudarshan/watchlist/AllListsSheet'
import { ArrangeSheets, useArrange } from '@/sudarshan/watchlist/ArrangeSheet'
import { FollowRowSheet } from '@/sudarshan/watchlist/FollowRowSheet'
import type { FollowForm } from '@/sudarshan/watchlist/followRows'
import { DeleteDialog, NameDialog } from '@/sudarshan/watchlist/ListDialogs'
import { ListStrip, ownId, ownRef, tabsFor } from '@/sudarshan/watchlist/ListStrip'
import { LISTS_FULL, ListTabs, PANEL_ID, tabId } from '@/sudarshan/watchlist/ListTabs'
import { LiveList } from '@/sudarshan/watchlist/LiveList'
import { copyName, copyPreset } from '@/sudarshan/watchlist/PresetLine'
import { SearchSheet } from '@/sudarshan/watchlist/SearchSheet' // F13
import { SectionSheets } from '@/sudarshan/watchlist/SectionMenus'
import type { OrderRequest } from '@/sudarshan/watchlist/SymbolSheet'
import { useOverlay } from '@/sudarshan/watchlist/useOverlay'
import { MAX_ITEMS, MAX_LISTS, useWatchlists } from '@/sudarshan/watchlist/useWatchlists'
import { useViewPref } from '@/sudarshan/watchlist/ViewSheet'
import { BUILT_IN } from '@/sudarshan/watchlist/v2/arrange'
import { useAlertOffers } from '@/sudarshan/watchlist/v2/listAlert'
import { FLAG_LIST_WORDS, flagName, itemsOf, renameFlag } from '@/sudarshan/watchlist/v2/marks'
import { kindOfRef } from '@/sudarshan/watchlist/v2/menuRules'
import { groupsOf, SECTIONS_FULL, useCollapsed } from '@/sudarshan/watchlist/v2/sections'
import { LIMITS, type RefKind } from '@/sudarshan/watchlist/v2/types'
import { useListView, useV2State } from '@/sudarshan/watchlist/v2/useListView'
import { usePush } from '@/sudarshan/watchlist/v2/usePush'
import { toast } from '@/utils/toast'

/** The list that was open last on this device: a convenience, so it lives in this browser only. */
export const OPEN_LIST_KEY = 'sudarshan-watchlist-open'

function readOpenList(): number | null {
  try {
    const id = Number(localStorage.getItem(OPEN_LIST_KEY))
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

function saveOpenList(id: number): void {
  try {
    localStorage.setItem(OPEN_LIST_KEY, String(id))
  } catch {
    // Storage refused (a private window): the choice lasts for the visit.
  }
}

const NO_ITEMS: WatchlistItem[] = []
const flagNumber = (ref: string) => Number(ref.slice(5))

/** M8.6: while another list is open, only these sheets act on what is on screen; the rest act on the own list. */
const OWN_ONLY = new Set([
  'add',
  'delete',
  'section',
  'sectionName',
  'sectionRemove',
  'pickSection',
  'pickList',
  'followRow', // F11
  'follow', // F11
])
const actsOnScreen = (kind: string, flag: boolean) =>
  !OWN_ONLY.has(kind) && (kind !== 'rename' || flag)

const HEADER_BUTTON =
  'inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/** Why Add is off for a full list, in a trader's words. */
export const LIST_FULL = `This list holds ${MAX_ITEMS} symbols, the most a list can. Remove one to add another.`

export default function Watchlist() {
  // Prices and search need the API key and today's Upstox login; the key is
  // null until both are there, and the notice says which is missing.
  const access = usePriceAccess()
  const { apiKey } = access
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)

  const watchlists = useWatchlists()
  // M8.5: the favourite tabs and the All lists sheet, only once the server says v2 is on.
  const v2Read = useV2State()
  const v2State = v2Read.data
  const v2 = v2State?.enabled === true
  // The store is newer than this app or cannot be read: the server's sentence is shown, so the
  // page never quietly becomes today's page without saying why the new features are missing.
  const recordSaid = v2Read.error?.code === 'record' ? v2Read.error.message : null
  usePush(v2)
  // SUDARSHAN M9 UI7: the lists a screener feeds, who it put in the open one, and its pushes.
  const queryClient = useQueryClient()
  const { socket } = useSocketContext()
  const fed = useFedLists()
  useScreenerPush(queryClient, fed.size > 0 ? socket : null)
  const fedIds = useMemo(() => new Set(fed.keys()), [fed])
  const [chosen, setChosen] = useState<number | null>(readOpenList)
  const active = watchlists.lists.find((list) => list.id === chosen) ?? watchlists.lists[0] ?? null
  const items = active?.items ?? NO_ITEMS
  const members = useListMembership(active?.id ?? null, active ? fed.has(active.id) : false)
  const full = items.length >= MAX_ITEMS
  const [editing, setEditing] = useState(false)
  // A favourite that is not one of his own lists (built-in, ranked, flag): its rows come in later changes.
  const [otherRef, setOtherRef] = useState<string | null>(null)
  // F8 (v2): a flag list is read from `view` alone: its rows are the flagged symbols, newest flag first.
  const flagRef = v2 && otherRef?.startsWith('flag:') ? otherRef : null
  const flagView = useListView(flagRef).data
  const flagItems = useMemo(() => itemsOf(flagView), [flagView])
  // F10 (v2): a built-in list is read from `view` alone, as a flag list is: locked, in the master's order.
  const presetRef = v2 && otherRef?.startsWith('preset:') ? otherRef : null
  const presetRead = useListView(presetRef)
  const presetView = presetRead.data
  const presetItems = useMemo(() => itemsOf(presetView), [presetView])
  const [copyKeys, setCopyKeys] = useState<readonly string[] | null>(null)
  const shownItems = flagRef ? flagItems : presetRef ? presetItems : items

  // What is open over the list lives in the address, so Back closes it.
  const { overlay, open, swap, close } = useOverlay(v2)
  const openItemId = overlay?.kind === 'item' ? overlay.itemId : null
  const order = overlay?.kind === 'order' ? overlay.order : null
  const alertRequest = overlay?.kind === 'alert' ? overlay.alert : null // SUDARSHAN M10.2
  const navigate = useNavigate()

  // An overlay that cannot be shown (an instrument no longer in the list, a
  // ticket or an alert form without its instrument, a list action with no
  // list) is closed once the lists are known, rather than left open over nothing.
  const settled = !watchlists.loading
  const orphan =
    overlay !== null &&
    settled &&
    ((overlay.kind === 'item' && !shownItems.some((item) => item.id === overlay.itemId)) ||
      (overlay.kind === 'copy' && (presetRef === null || copyKeys === null)) || // F10
      (otherRef !== null && !actsOnScreen(overlay.kind, flagRef !== null)) || // M8.6
      (overlay.kind === 'order' && overlay.order === null) ||
      (overlay.kind === 'follow' && overlay.form === null) || // F11
      (overlay.kind === 'alert' && overlay.alert === null) ||
      (overlay.kind !== 'item' &&
        overlay.kind !== 'order' &&
        overlay.kind !== 'alert' &&
        overlay.kind !== 'follow' &&
        overlay.kind !== 'new' &&
        overlay.kind !== 'copy' &&
        !active))
  useEffect(() => {
    if (orphan) close()
  }, [orphan, close])

  const held = useMemo(
    () => new Set(items.map((item) => keyOf(item.symbol, item.exchange))),
    [items]
  )

  const select = useCallback((listId: number) => {
    setChosen(listId)
    saveOpenList(listId)
    setOtherRef(null)
    setEditing(false)
  }, [])
  const selectRef = useCallback(
    (ref: string) => {
      const id = ownId(ref)
      if (id !== null) select(id)
      else {
        setOtherRef(ref)
        setEditing(false)
      }
    },
    [select]
  )
  const tabs = useMemo(() => {
    if (!v2) return []
    const shown = tabsFor(v2State?.favourites ?? [], watchlists.lists)
    const open = active ? ownRef(active.id) : null
    // The open list always has a tab, even when it is not a favourite.
    // F10: a built-in list opened from All lists has a tab too, once its view says its name.
    if (presetRef && presetView && !shown.some((tab) => tab.ref === presetRef))
      return [
        ...shown,
        { ref: presetRef, kind: 'preset' as const, name: presetView.name, count: presetView.total },
      ]
    return open && !otherRef && !shown.some((tab) => tab.ref === open) && active
      ? [
          ...shown,
          { ref: open, kind: 'list' as const, name: active.name, count: active.items.length },
        ]
      : shown
  }, [v2, v2State?.favourites, watchlists.lists, active, otherRef, presetRef, presetView])
  // M8.6: a favourite that is not an own list is never answered with the own list behind it, tab or no tab.
  const foreign =
    v2 && otherRef !== null
      ? (tabs.find((tab) => tab.ref === otherRef) ??
        (presetRef || flagRef
          ? null
          : {
              ref: otherRef,
              kind: kindOfRef(otherRef) as RefKind,
              name: 'this list',
              count: null,
            }))
      : null
  const activeRef =
    flagRef ?? presetRef ?? (foreign ? foreign.ref : active ? ownRef(active.id) : null)

  const activeId = active?.id ?? null
  // F17: "Alert on this list" is for the list on show: an own list, a built-in, ranked or flag one.
  const alertFor = useAlertOffers(v2, fedIds)
  const listAlert = flagRef
    ? alertFor(
        flagRef,
        tabs.find((tab) => tab.ref === flagRef)?.name ?? 'Flag',
        flagItems.length,
        'flag'
      )
    : foreign
      ? !tabs.some((tab) => tab.ref === foreign.ref)
        ? undefined // M8.6: a list whose name is not known has no alert address
        : alertFor(
            foreign.ref,
            foreign.name,
            (presetRef ? presetView?.total : undefined) ?? foreign.count ?? undefined, // F10: not known is not 0
            foreign.kind
          )
      : presetRef
        ? undefined // F10: its tab is not there until the list is read
        : active
          ? alertFor(ownRef(active.id), active.name, items.length)
          : undefined
  // F3 (v2): the open own list in sections, read from the `view` op; flat while it is not read.
  const sectionsRef = v2 && active && otherRef === null ? ownRef(active.id) : null
  const view = useListView(sectionsRef).data
  // F11: a row's sheet or form for a row the list no longer has (removed here or on another screen) is closed.
  const followGone =
    (overlay?.kind === 'followRow' ||
      (overlay?.kind === 'follow' && overlay.form?.mode !== 'add' && overlay.form !== null)) &&
    view !== undefined &&
    !view.follow.some(
      (row) =>
        row.id ===
        (overlay.kind === 'followRow' ? overlay.rowId : (overlay.form as { rowId: number }).rowId)
    )
  useEffect(() => {
    if (followGone) close()
  }, [followGone, close])
  const openFollowRow = useCallback((rowId: number) => open({ kind: 'followRow', rowId }), [open])
  // Over a sheet the form takes its place; over the list it opens as a sheet of its own.
  const hasOverlay = overlay !== null
  const openFollowForm = useCallback(
    (form: FollowForm) =>
      hasOverlay ? swap({ kind: 'follow', form }) : open({ kind: 'follow', form }),
    [hasOverlay, swap, open]
  )
  const followRows = useMemo(
    () =>
      view && sectionsRef
        ? {
            rules: view.follow,
            openRowId: overlay?.kind === 'followRow' ? overlay.rowId : null,
            onOpenRow: openFollowRow,
            onForm: openFollowForm,
          }
        : undefined,
    [view, sectionsRef, overlay, openFollowRow, openFollowForm]
  )
  const folds = useCollapsed(sectionsRef)
  // F6; F10: a built-in list has a View of its own (sort and basis), saved under its ref.
  const prefRef = sectionsRef ?? (presetView ? presetRef : null)
  const viewPref = useViewPref(prefRef, sectionsRef ? view : presetView)
  const sectionLayout = useMemo(
    () =>
      view && sectionsRef
        ? {
            groups: groupsOf(view),
            collapsed: folds.collapsed,
            onToggle: folds.toggle,
            onMenu: (sectionId: number) => open({ kind: 'section', sectionId }),
          }
        : undefined,
    [view, sectionsRef, folds.collapsed, folds.toggle, open]
  )
  // F5 (v2): edit mode as a draft saved with one `arrange`, the symbol sheet's actions, the pickers.
  const arrange = useArrange({
    ref: sectionsRef,
    listId: active?.id ?? null,
    view,
    items,
    editing,
    open: (id) => open({ kind: 'pickSection', item: id }),
  })
  const symbolActions = useMemo(
    () =>
      view && sectionsRef
        ? {
            onSection:
              view.sections.length > 0
                ? (item: WatchlistItem) => swap({ kind: 'pickSection', item: item.id })
                : undefined,
            onCopy: (item: WatchlistItem) =>
              swap({ kind: 'pickList', item: item.id, section: null, move: false }),
          }
        : undefined,
    [view, sectionsRef, swap]
  )
  const { move, removeItem: removeFromList } = watchlists
  const openSheet = useCallback((itemId: number) => open({ kind: 'item', itemId }), [open])
  const moveItem = useCallback(
    (item: WatchlistItem, by: -1 | 1) => {
      if (activeId !== null) void move(activeId, item.id, by)
    },
    [activeId, move]
  )
  const removeItem = useCallback(
    (item: WatchlistItem) => {
      if (activeId === null) return
      if (openItemId === item.id) close()
      void removeFromList(activeId, item)
    },
    [activeId, openItemId, close, removeFromList]
  )
  const startAdding = useMemo(
    () => (apiKey === null || full ? undefined : () => open({ kind: 'add' })),
    [apiKey, full, open]
  )
  const placeOrder = useCallback(
    (request: OrderRequest) => swap({ kind: 'order', order: request }),
    [swap]
  )
  // The sheet's Set alert swaps it for the alert form, as Buy swaps it for the ticket.
  const setAlert = useCallback(
    (request: AlertPrefill) => swap({ kind: 'alert', alert: request }),
    [swap]
  )
  const deleting = overlay?.kind === 'delete'
  const presetPanel = useMemo(
    () => ({ id: PANEL_ID, role: 'tabpanel' as const, 'aria-label': presetView?.name }),
    [presetView?.name]
  )
  // M8.6: the list to delete is the one on screen when the confirmation opened. If another list
  // comes on screen while it is open, it closes and nothing is sent.
  const [doomedId, setDoomedId] = useState<number | null>(null)
  const doomedList = watchlists.lists.find((list) => list.id === doomedId) ?? null
  const doomed = useMemo(
    () =>
      deleting && doomedList ? { name: doomedList.name, count: doomedList.items.length } : null,
    [deleting, doomedList]
  )
  const onScreenId = otherRef === null ? activeId : null
  useEffect(() => {
    if (!deleting) setDoomedId(null)
    else if (doomedId === null) {
      if (onScreenId === null) close()
      else setDoomedId(onScreenId) // a link to the confirmation: the list on screen now
    } else if (doomedList !== null && onScreenId !== doomedId) close()
  }, [deleting, doomedId, doomedList, onScreenId, close])
  const panel = useMemo(
    () =>
      active
        ? { id: PANEL_ID, role: 'tabpanel' as const, 'aria-labelledby': tabId(active.id) }
        : { 'aria-label': 'Watchlist' },
    [active]
  )

  return (
    <div className="flex flex-col gap-2">
      <MarketCalendarSource key={day} onChange={setMarket} />

      <div className="flex items-center justify-between gap-3">
        <h1 className="sd-display text-[1.75rem] leading-none font-bold md:text-[2rem]">
          Watchlist
        </h1>
        {editing ? (
          <button
            type="button"
            onClick={() => setEditing(false)}
            className={cn(HEADER_BUTTON, 'border border-input bg-card hover:bg-accent')}
          >
            <Check aria-hidden="true" className="size-5" />
            Done
          </button>
        ) : presetRef ? null /* F10: a built-in list has no Add */ : (
          <button
            type="button"
            disabled={!active || apiKey === null || full || otherRef !== null}
            aria-describedby={full ? 'sd-watchlist-full' : undefined}
            onClick={() => open({ kind: 'add' })}
            className={cn(HEADER_BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            <Plus aria-hidden="true" className="size-5" />
            Add
          </button>
        )}
      </div>

      {full && !editing && !presetRef && (
        <p id="sd-watchlist-full" className="px-1 text-xs font-medium text-muted-foreground">
          {LIST_FULL}
        </p>
      )}

      <AccessNotice access={access} />

      {recordSaid && (
        <section
          role="alert"
          className="rounded-[1.125rem] border border-[color:var(--down)] bg-card px-4 py-3.5"
        >
          <p className="text-sm font-semibold">{recordSaid}</p>
        </section>
      )}

      {watchlists.error && watchlists.lists.length === 0 ? (
        <section
          role="alert"
          className="flex flex-col items-start gap-3 rounded-[1.125rem] border border-[color:var(--down)] bg-card px-4 py-3.5"
        >
          <p className="text-sm font-semibold">{watchlists.error}</p>
          <button
            type="button"
            onClick={() => void watchlists.retry()}
            className={cn(HEADER_BUTTON, 'border border-input bg-card hover:bg-accent')}
          >
            <RefreshCw aria-hidden="true" className="size-4" />
            Try again
          </button>
        </section>
      ) : (
        <>
          {active && (
            <ListTabs
              lists={watchlists.lists}
              activeId={active.id}
              editing={editing}
              onSelect={select}
              onCreate={() => open({ kind: 'new' })}
              onRename={() => open({ kind: 'rename' })}
              onEdit={() => setEditing((on) => !on)}
              onDelete={() => {
                setDoomedId(onScreenId)
                open({ kind: 'delete' })
              }}
              fed={fedIds}
              onNewSection={
                sectionsRef ? () => open({ kind: 'sectionName', sectionId: null }) : undefined
              }
              onView={prefRef ? () => open({ kind: 'view' }) : undefined} // F6, F10
              kind={kindOfRef(otherRef)} // M8.6: the menu follows the list on screen
              alert={listAlert} // F17
              newSectionOff={
                view && view.sections.length >= LIMITS.sections_per_list ? SECTIONS_FULL : null
              }
              strip={
                v2 ? (
                  <ListStrip
                    tabs={tabs}
                    activeRef={activeRef}
                    fed={fedIds}
                    onSelect={selectRef}
                    onAll={() => open({ kind: 'lists' })}
                  />
                ) : undefined
              }
            />
          )}

          {
            flagRef && (
              <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
                {FLAG_LIST_WORDS}
              </p>
            ) /* F8 */
          }
          {presetRef && !presetView ? (
            <section
              id={PANEL_ID}
              role="tabpanel"
              aria-label={foreign?.name ?? 'Built-in list'}
              className="flex flex-col items-start gap-3 rounded-[1.125rem] border bg-card px-4 py-6 text-sm font-medium"
            >
              {presetRead.isError ? (
                <>
                  <p role="alert" className="font-semibold">
                    {presetRead.error.message /* the server's sentence: never an empty list */}
                  </p>
                  {presetRead.error.code !== 'gone' && (
                    <button
                      type="button"
                      onClick={() => void presetRead.refetch()}
                      className={cn(HEADER_BUTTON, 'border border-input bg-card hover:bg-accent')}
                    >
                      <RefreshCw aria-hidden="true" className="size-4" />
                      Try again
                    </button>
                  )}
                </>
              ) : (
                <output>Reading the list.</output>
              )}
            </section>
          ) : foreign && !flagRef && !presetRef ? (
            <section
              id={PANEL_ID}
              role="tabpanel"
              aria-label={foreign.name}
              className="rounded-[1.125rem] border bg-card px-4 py-6 text-center text-sm font-medium"
            >
              The rows of {foreign.name} are not shown on this screen yet.
              {foreign.kind !== 'list' && <span className="mt-2 block">{BUILT_IN}</span>}
            </section>
          ) : (
            <LiveList
              items={shownItems}
              apiKey={apiKey}
              isOpen={isOpen}
              loading={watchlists.loading}
              editing={editing && !flagRef}
              listName={(presetRef ? presetView?.name : active?.name) ?? 'the list'}
              openItemId={openItemId}
              panel={presetRef ? presetPanel : panel}
              onOpen={openSheet}
              onMove={moveItem}
              onRemove={removeItem}
              onAdd={otherRef ? undefined : startAdding}
              onCloseSheet={close}
              onOrder={placeOrder}
              onAlert={setAlert}
              members={flagRef || presetRef ? undefined : members}
              sections={sectionLayout}
              arrange={arrange.edit} // F5
              symbolActions={symbolActions} // F5
              viewPref={prefRef ? viewPref : undefined} // F6, F10
              viewOpen={overlay?.kind === 'view'} // F6
              view={flagRef ? flagView : sectionsRef ? view : presetRef ? presetView : undefined} // F7, F8, F10
              flagNames={v2 ? (v2State?.flag_names ?? []) : undefined} // F8
              noRemove={flagRef !== null || presetRef !== null} // F8, F10
              followRows={flagRef || presetRef ? undefined : followRows} // F11
              picker={
                v2 && overlay?.kind === 'strikes' && overlay.pick && active && otherRef === null
                  ? {
                      ...overlay.pick,
                      listName: active.name,
                      full,
                      onAdd: async (symbol, exchange) => {
                        await watchlists.addItem(active.id, symbol, exchange)
                      },
                      onFollow: openFollowForm,
                      onClose: close,
                    }
                  : undefined
              } // F14
              preset={
                presetView
                  ? {
                      lines: presetView.lines,
                      onCopy: (keys) => {
                        setCopyKeys(keys)
                        open({ kind: 'copy' })
                      },
                    }
                  : undefined
              } // F10
            />
          )}
        </>
      )}

      {v2 && (
        <AllListsSheet
          open={overlay?.kind === 'lists'}
          onClose={close}
          fed={fedIds}
          activeRef={activeRef}
          onPick={(ref) => {
            selectRef(ref)
            close()
          }}
          onNew={() => swap({ kind: 'new' })}
          newOff={watchlists.lists.length >= MAX_LISTS ? LISTS_FULL : null}
        />
      )}

      {v2 && (
        <SectionSheets
          overlay={overlay}
          view={view}
          listId={active?.id ?? null}
          alertFor={alertFor}
          onSwap={swap}
          onClose={close}
        />
      )}

      {v2 && (
        <ArrangeSheets
          overlay={overlay}
          view={view}
          items={items}
          listId={active?.id ?? null}
          sendTo={arrange.sendTo}
          sectionOf={arrange.sectionOf}
          onClose={close}
        />
      )}

      {v2 && active !== null && (
        // F13: with the switch on, the search sheet takes the place of today's Add sheet.
        <SearchSheet
          open={overlay?.kind === 'add' && otherRef === null}
          onOpenChange={(next) => {
            if (!next) close()
          }}
          listId={active.id}
          listName={active.name}
          held={held}
          full={full}
          onAdd={async (symbol, exchange) => {
            await watchlists.addItem(active.id, symbol, exchange)
          }}
          onFollow={openFollowForm}
          onPickStrike={(underlying, exchange) =>
            swap({ kind: 'strikes', pick: { underlying, exchange } })
          } // F14
        />
      )}

      <AddSymbolSheet
        open={!v2 && overlay?.kind === 'add' && active !== null && otherRef === null}
        onOpenChange={(next) => {
          if (!next) close()
        }}
        apiKey={apiKey}
        listName={active?.name ?? 'the list'}
        held={held}
        full={full}
        onAdd={async (result) => {
          if (active) await watchlists.addItem(active.id, result.symbol, result.exchange)
        }}
      />

      <NameDialog
        mode={
          overlay?.kind === 'copy' && presetView && copyKeys
            ? 'copy'
            : overlay?.kind === 'new'
              ? 'create'
              : overlay?.kind === 'rename'
                ? flagRef
                  ? 'flag'
                  : otherRef === null
                    ? 'rename'
                    : null
                : null
        }
        current={
          overlay?.kind === 'copy' && presetView
            ? copyName(presetView.name)
            : flagRef
              ? flagName(v2State?.flag_names, flagNumber(flagRef))
              : (active?.name ?? '')
        }
        onClose={close}
        onSubmit={async (name) => {
          if (overlay?.kind === 'copy' && presetRef && copyKeys) {
            // F10: every key in the order shown; the server's words (the 250 cut) are shown as sent.
            const copied = await copyPreset(queryClient, presetRef, copyKeys, name)
            toast.success(copied.words)
            select(copied.done.list)
            return
          }
          if (overlay?.kind === 'rename' && flagRef) {
            // M8.6: the flag on screen, by the existing flag rename op, on the flags version read.
            await renameFlag(
              queryClient,
              v2State?.flag_names,
              v2State?.versions?.flags ?? 0,
              flagNumber(flagRef),
              name
            )
            return
          }
          if (overlay?.kind === 'rename' && active && otherRef === null) {
            await watchlists.rename(active.id, name)
          } else {
            const created = await watchlists.create(name)
            select(created.id)
          }
        }}
      />

      <DeleteDialog
        list={doomed}
        onClose={close}
        onConfirm={async () => {
          if (doomedId === null || doomedId !== onScreenId) return
          const next = await watchlists.remove(doomedId)
          if (next !== null) select(next)
        }}
      />

      {v2 && (
        <FollowRowSheet
          form={overlay?.kind === 'follow' ? overlay.form : null}
          view={view}
          onClose={close}
          onSwap={(form) => swap({ kind: 'follow', form })}
        />
      )}

      <PlaceOrderDialog
        open={order !== null}
        onOpenChange={(next) => {
          if (!next) close()
        }}
        symbol={order?.item.symbol ?? ''}
        exchange={order?.item.exchange ?? ''}
        action={order?.action ?? 'BUY'}
        lotSize={order?.contract.lotSize ?? 1}
        tickSize={order?.contract.tickSize ?? 0.05}
        product={order?.product}
        strategy="Watchlist"
      />

      {alertRequest && (
        <AlertFormSheet
          prefill={alertRequest}
          onClose={close}
          onSaved={(saved) => {
            close()
            toastAlertSet(saved, navigate)
          }}
        />
      )}
    </div>
  )
}
