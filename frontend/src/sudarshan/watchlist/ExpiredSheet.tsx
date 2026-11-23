import { useRef } from 'react'
import type { WatchlistItem } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { BottomSheet } from './BottomSheet'
import { describe, expiredWords } from './rows'
import { SymbolMarks } from './SymbolMarks'
import type { FlagName } from './v2/types'

const BUTTON =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-5 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/**
 * M8.2 TD1: the sheet of a row whose contract has expired, in place of the symbol sheet: nothing
 * to quote, chart or trade, so it says when it expired and asks before it removes the row. The
 * removal is sent once however it is tapped. A list that cannot be changed only says when (its own
 * close button and Back close it).
 */
export function ExpiredSheet({
  item,
  day,
  listName,
  flags,
  onClose,
  onRemove,
}: {
  item: WatchlistItem
  /** The day it expired, 2026-11-03. */
  day: string
  listName: string
  /** v2 (F8): the flag names, so its flag and note stay in reach; absent with the switch off. */
  flags?: readonly FlagName[]
  onClose: () => void
  onRemove?: (item: WatchlistItem) => void
}) {
  const sent = useRef(false)
  const named = describe(item.symbol, item.exchange)
  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={named.title}
      description={expiredWords(day)}
    >
      <div data-expired-sheet="true" className="flex flex-col gap-3">
        <p className="text-sm font-medium">
          {onRemove
            ? `It is no longer quoted or traded. Remove it from ${listName}?`
            : 'It is no longer quoted or traded.'}
        </p>
        {onRemove && (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className={cn(BUTTON, 'border border-input bg-card hover:bg-accent')}
            >
              Keep it
            </button>
            <button
              type="button"
              onClick={() => {
                if (sent.current) return
                sent.current = true
                onRemove(item)
              }}
              className={cn(BUTTON, 'bg-destructive text-white hover:bg-destructive/90')}
            >
              Remove
            </button>
          </div>
        )}
        {flags && (
          // v0.17.0 review L6: Expired is said once, above, with its day.
          <SymbolMarks
            k={`${item.exchange}:${item.symbol}`.toUpperCase()}
            names={flags}
            expiredSaid
          />
        )}
      </div>
    </BottomSheet>
  )
}
