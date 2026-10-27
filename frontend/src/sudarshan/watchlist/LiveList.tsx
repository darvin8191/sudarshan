import { memo } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import type { AlertPrefill } from '@/sudarshan/alerts/AlertForm'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import type { MarketOpen } from '@/sudarshan/dashboard/live'
import { type OrderRequest, SymbolSheet } from './SymbolSheet'
import { useLiveRows } from './useLiveRows'
import { WatchlistRows } from './WatchlistRows'

/**
 * The part of the watchlist that moves with the market: the line over the
 * list, the rows and the open instrument's sheet. It holds the live data
 * itself, so a tick redraws this and nothing above it (the tabs, the header,
 * the dialogs), and it draws at most once a frame (`useFrameLines`); each row
 * redraws only when its own figures change.
 */
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
}) {
  const live = useLiveRows({ items, apiKey, marketOpen: isOpen })
  const sheetRow =
    openItemId === null ? null : (live.rows.find((row) => row.item.id === openItemId) ?? null)
  const nothingShown = live.rows.length > 0 && live.rows.every((row) => row.view.ltp === null)
  const readFailed = apiKey !== null && live.snapshot === 'error' && live.rows.length > 0

  return (
    <>
      {live.line && <LiveLine line={live.line} now={live.now} className="flex min-h-5 px-1" />}

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

      <section
        {...panel}
        className="overflow-hidden rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]"
      >
        <WatchlistRows
          rows={live.rows}
          loading={loading}
          editing={editing}
          onOpen={onOpen}
          onMove={onMove}
          onRemove={onRemove}
          onAdd={onAdd}
        />
      </section>

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
      />
    </>
  )
})
