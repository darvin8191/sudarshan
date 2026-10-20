// SUDARSHAN (M8.1, M8.2, M8.3): the phone-first watchlist, the first screen on
// the bottom bar as in a broker's app. The lists are the server-side ones the
// /trading terminal's panel uses (blueprints/watchlist.py), the prices stream
// tick by tick on the tab's shared market-data socket, and a tap opens the
// instrument's sheet: Buy and Sell, chart, option chain and depth.
import { Check, KeyRound, Plus, RefreshCw } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'
import type { WatchlistItem } from '@/api/watchlist'
import { PlaceOrderDialog } from '@/components/trading/PlaceOrderDialog'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { anyOpen, keyOf } from '@/sudarshan/dashboard/live'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { AddSymbolSheet } from '@/sudarshan/watchlist/AddSymbolSheet'
import type { Contract } from '@/sudarshan/watchlist/api'
import { DeleteDialog, NameDialog } from '@/sudarshan/watchlist/ListDialogs'
import { ListTabs, PANEL_ID, tabId } from '@/sudarshan/watchlist/ListTabs'
import { SymbolSheet } from '@/sudarshan/watchlist/SymbolSheet'
import { useLiveRows } from '@/sudarshan/watchlist/useLiveRows'
import { useWatchlists } from '@/sudarshan/watchlist/useWatchlists'
import { WatchlistRows } from '@/sudarshan/watchlist/WatchlistRows'

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

/** The exchanges whose opening and closing read the snapshot again, the same for every list. */
const MAIN_EXCHANGES = ['NSE', 'BSE', 'NFO', 'BFO', 'MCX', 'CDS']

const NO_ITEMS: WatchlistItem[] = []

interface OrderIntent {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  contract: Contract
}

const HEADER_BUTTON =
  'inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'

export default function Watchlist() {
  const apiKey = useAuthStore((state) => state.apiKey)
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { isOpen } = useMarketOpen(market)

  const watchlists = useWatchlists()
  const [chosen, setChosen] = useState<number | null>(readOpenList)
  const active = watchlists.lists.find((list) => list.id === chosen) ?? watchlists.lists[0] ?? null
  const items = active?.items ?? NO_ITEMS

  const [editing, setEditing] = useState(false)
  const [openItem, setOpenItem] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  const [naming, setNaming] = useState<'create' | 'rename' | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [order, setOrder] = useState<OrderIntent | null>(null)

  const live = useLiveRows({
    items,
    apiKey,
    marketOpen: isOpen,
    anyMarketOpen: anyOpen(MAIN_EXCHANGES.map((exchange) => isOpen(exchange))),
  })
  const sheetRow = live.rows.find((row) => row.item.id === openItem) ?? null
  const held = useMemo(
    () => new Set(items.map((item) => keyOf(item.symbol, item.exchange))),
    [items]
  )

  const select = useCallback((listId: number) => {
    setChosen(listId)
    saveOpenList(listId)
    setEditing(false)
    setOpenItem(null)
  }, [])

  const removeItem = (item: WatchlistItem) => {
    if (!active) return
    if (openItem === item.id) setOpenItem(null)
    void watchlists.removeItem(active.id, item)
  }

  const noPrices =
    apiKey !== null &&
    live.snapshot === 'error' &&
    live.rows.length > 0 &&
    live.rows.every((row) => row.view.ltp === null)

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
            disabled={!active || apiKey === null}
            onClick={() => setAdding(true)}
            className={cn(HEADER_BUTTON, 'bg-primary text-primary-foreground hover:bg-primary/90')}
          >
            <Plus aria-hidden="true" className="size-5" />
            Add
          </button>
        )}
      </div>

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
              onCreate={() => setNaming('create')}
              onRename={() => setNaming('rename')}
              onEdit={() => setEditing((on) => !on)}
              onDelete={() => setDeleting(true)}
            />
          )}

          {live.line && <LiveLine line={live.line} now={live.now} className="flex min-h-5 px-1" />}

          {noPrices && (
            <p className="flex flex-wrap items-center gap-x-2 px-1 text-xs font-medium text-muted-foreground">
              Prices are not available right now.
              <button
                type="button"
                onClick={live.retry}
                className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
              >
                Try again
              </button>
            </p>
          )}

          <section
            id={active ? PANEL_ID : undefined}
            role={active ? 'tabpanel' : undefined}
            aria-labelledby={active ? tabId(active.id) : undefined}
            aria-label={active ? undefined : 'Watchlist'}
            className="overflow-hidden rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]"
          >
            <WatchlistRows
              rows={live.rows}
              loading={watchlists.loading}
              editing={editing}
              onOpen={setOpenItem}
              onMove={(item, by) => active && void watchlists.move(active.id, item.id, by)}
              onRemove={removeItem}
              onAdd={apiKey === null ? undefined : () => setAdding(true)}
            />
          </section>
        </>
      )}

      <SymbolSheet
        row={sheetRow}
        listName={active?.name ?? 'the list'}
        apiKey={apiKey}
        line={live.line}
        marketOpen={sheetRow ? isOpen(sheetRow.item.exchange) : null}
        feedTickAt={live.feedTickAt}
        now={live.now}
        onClose={() => setOpenItem(null)}
        onOrder={(item, action, contract) => {
          setOpenItem(null)
          setOrder({ symbol: item.symbol, exchange: item.exchange, action, contract })
        }}
        onRemove={removeItem}
      />

      <AddSymbolSheet
        open={adding && active !== null}
        onOpenChange={setAdding}
        apiKey={apiKey}
        listName={active?.name ?? 'the list'}
        held={held}
        onAdd={async (result) => {
          if (active) await watchlists.addItem(active.id, result.symbol, result.exchange)
        }}
      />

      <NameDialog
        mode={naming}
        current={active?.name ?? ''}
        onClose={() => setNaming(null)}
        onSubmit={async (name) => {
          if (naming === 'rename' && active) {
            await watchlists.rename(active.id, name)
          } else {
            const created = await watchlists.create(name)
            select(created.id)
          }
        }}
      />

      <DeleteDialog
        list={deleting && active ? { name: active.name, count: active.items.length } : null}
        onClose={() => setDeleting(false)}
        onConfirm={async () => {
          if (!active) return
          const next = await watchlists.remove(active.id)
          if (next !== null) select(next)
        }}
      />

      <PlaceOrderDialog
        open={order !== null}
        onOpenChange={(open) => {
          if (!open) setOrder(null)
        }}
        symbol={order?.symbol ?? ''}
        exchange={order?.exchange ?? ''}
        action={order?.action ?? 'BUY'}
        lotSize={order?.contract.lotSize ?? 1}
        tickSize={order?.contract.tickSize ?? 0.05}
        strategy="Watchlist"
      />
    </div>
  )
}
