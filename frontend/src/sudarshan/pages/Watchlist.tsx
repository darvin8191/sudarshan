// SUDARSHAN (M8.1, M8.2, M8.3): the phone-first watchlist, the first screen on
// the bottom bar as in a broker's app. The lists are the server-side ones the
// /trading terminal's panel uses (blueprints/watchlist.py), the prices stream
// tick by tick on the tab's shared market-data socket, and a tap opens the
// instrument's sheet: Buy and Sell, chart, option chain and depth.
import { Check, KeyRound, Plus, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import type { WatchlistItem } from '@/api/watchlist'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { AlertFormSheet, type AlertPrefill, toastAlertSet } from '@/sudarshan/alerts/AlertForm'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { keyOf } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { AddSymbolSheet } from '@/sudarshan/watchlist/AddSymbolSheet'
import { DeleteDialog, NameDialog } from '@/sudarshan/watchlist/ListDialogs'
import { ListTabs, PANEL_ID, tabId } from '@/sudarshan/watchlist/ListTabs'
import { LiveList } from '@/sudarshan/watchlist/LiveList'
import type { OrderRequest } from '@/sudarshan/watchlist/SymbolSheet'
import { useOverlay } from '@/sudarshan/watchlist/useOverlay'
import { MAX_ITEMS, useWatchlists } from '@/sudarshan/watchlist/useWatchlists'

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

const HEADER_BUTTON =
  'inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

/** Why Add is off for a full list, in a trader's words. */
export const LIST_FULL = `This list holds ${MAX_ITEMS} symbols, the most a list can. Remove one to add another.`

export default function Watchlist() {
  const apiKey = useAuthStore((state) => state.apiKey)
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)

  const watchlists = useWatchlists()
  const [chosen, setChosen] = useState<number | null>(readOpenList)
  const active = watchlists.lists.find((list) => list.id === chosen) ?? watchlists.lists[0] ?? null
  const items = active?.items ?? NO_ITEMS
  const full = items.length >= MAX_ITEMS
  const [editing, setEditing] = useState(false)

  // What is open over the list lives in the address, so Back closes it.
  const { overlay, open, swap, close } = useOverlay()
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
    ((overlay.kind === 'item' && !items.some((item) => item.id === overlay.itemId)) ||
      (overlay.kind === 'order' && overlay.order === null) ||
      (overlay.kind === 'alert' && overlay.alert === null) ||
      (overlay.kind !== 'item' &&
        overlay.kind !== 'order' &&
        overlay.kind !== 'alert' &&
        overlay.kind !== 'new' &&
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
    setEditing(false)
  }, [])

  const activeId = active?.id ?? null
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
  const activeName = active?.name
  const doomed = useMemo(
    () => (deleting && activeName !== undefined ? { name: activeName, count: items.length } : null),
    [deleting, activeName, items.length]
  )
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
        ) : (
          <button
            type="button"
            disabled={!active || apiKey === null || full}
            aria-describedby={full ? 'sd-watchlist-full' : undefined}
            onClick={() => open({ kind: 'add' })}
            className={cn(HEADER_BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            <Plus aria-hidden="true" className="size-5" />
            Add
          </button>
        )}
      </div>

      {full && !editing && (
        <p id="sd-watchlist-full" className="px-1 text-xs font-medium text-muted-foreground">
          {LIST_FULL}
        </p>
      )}

      {apiKey === null && (
        <div className="flex flex-col items-start gap-2 rounded-[1.125rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-4 py-3">
          <p className="flex items-center gap-2 text-sm font-semibold text-[color:var(--gold-ink)]">
            <KeyRound aria-hidden="true" className="size-4 shrink-0" />
            Prices and search need an API key.
          </p>
          <Link
            to="/apikey"
            className="inline-flex min-h-12 items-center text-sm font-semibold text-[color:var(--brand)] underline underline-offset-2"
          >
            Create one under API Key
          </Link>
        </div>
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
              onDelete={() => open({ kind: 'delete' })}
            />
          )}

          <LiveList
            items={items}
            apiKey={apiKey}
            isOpen={isOpen}
            loading={watchlists.loading}
            editing={editing}
            listName={active?.name ?? 'the list'}
            openItemId={openItemId}
            panel={panel}
            onOpen={openSheet}
            onMove={moveItem}
            onRemove={removeItem}
            onAdd={startAdding}
            onCloseSheet={close}
            onOrder={placeOrder}
            onAlert={setAlert}
          />
        </>
      )}

      <AddSymbolSheet
        open={overlay?.kind === 'add' && active !== null}
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
        mode={overlay?.kind === 'new' ? 'create' : overlay?.kind === 'rename' ? 'rename' : null}
        current={active?.name ?? ''}
        onClose={close}
        onSubmit={async (name) => {
          if (overlay?.kind === 'rename' && active) {
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
          if (!active) return
          const next = await watchlists.remove(active.id)
          if (next !== null) select(next)
        }}
      />

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
