/**
 * "Add to watchlist" from an option leg's own sheet (M11.1, design 5.5): the owner's lists with how
 * full each is; a tap adds the leg at the end of that list through the watchlists' own add
 * (`useWatchlists().addItem`, the call the Watchlist screen's Add makes), so the list is the same
 * on the phone and in the terminal. A list that holds the leg already, or is full, says so and
 * cannot be tapped. Nothing here is new on the server.
 */
import { Check, ListPlus } from 'lucide-react'
import { useState } from 'react'
import { watchlistError } from '@/api/watchlist'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { MAX_ITEMS, useWatchlists } from '@/sudarshan/watchlist/useWatchlists'
import { toast } from '@/utils/toast'

const ROW =
  'flex min-h-12 w-full items-center gap-3 rounded-xl px-3 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60'

export function AddToWatchlist({
  symbol,
  exchange,
  label,
  onClose,
}: {
  symbol: string
  exchange: string
  /** The leg in words: "NIFTY 25,900 CE". */
  label: string
  onClose: () => void
}) {
  const watchlists = useWatchlists()
  const [busy, setBusy] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const lists = watchlists.loading || watchlists.error ? null : watchlists.lists

  const add = async (id: number, name: string) => {
    if (busy !== null) return
    setBusy(id)
    setProblem(null)
    try {
      await watchlists.addItem(id, symbol, exchange)
      toast.success(`Added ${label} to ${name}`)
      onClose()
    } catch (error) {
      setProblem(
        watchlistError(
          error,
          `${label} could not be added to ${name}. Check the connection, then try again.`
        )
      )
      setBusy(null)
    }
  }

  return (
    <BottomSheet
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Add to watchlist"
      description={label}
    >
      <div data-add-to-list="true" className="flex flex-col gap-1">
        {watchlists.loading && (
          <p className="text-sm font-medium text-muted-foreground">Reading your watchlists.</p>
        )}
        {watchlists.error && (
          <p className="flex flex-wrap items-center gap-x-2 text-sm font-medium text-[color:var(--warning-text)]">
            Your watchlists could not be read.
            <button
              type="button"
              onClick={() => void watchlists.retry()}
              className="min-h-12 font-semibold text-[color:var(--brand)] underline underline-offset-2"
            >
              Try again
            </button>
          </p>
        )}
        {lists?.length === 0 && (
          <p className="text-sm font-medium text-[color:var(--text-secondary)]">
            You have no watchlist yet. Make one on the Watchlist screen, then add this here.
          </p>
        )}
        {lists?.map((list) => {
          const held = list.items.some(
            (item) => item.symbol === symbol && item.exchange === exchange
          )
          const full = list.items.length >= MAX_ITEMS
          return (
            <button
              key={list.id}
              type="button"
              disabled={held || full || busy !== null}
              onClick={() => void add(list.id, list.name)}
              className={ROW}
            >
              {held ? (
                <Check aria-hidden="true" className="size-5 shrink-0 text-[color:var(--up)]" />
              ) : (
                <ListPlus aria-hidden="true" className="size-5 shrink-0" />
              )}
              <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-semibold">
                {list.name}
              </span>
              <span className="shrink-0 text-xs font-medium text-[color:var(--text-secondary)]">
                {held
                  ? 'Already in this list'
                  : full
                    ? `Full: ${MAX_ITEMS} of ${MAX_ITEMS}`
                    : busy === list.id
                      ? 'Adding'
                      : `${list.items.length} of ${MAX_ITEMS}`}
              </span>
            </button>
          )
        })}
        {problem && (
          <p role="alert" className="px-1 text-[0.8125rem] font-semibold text-destructive">
            {problem}
          </p>
        )}
      </div>
    </BottomSheet>
  )
}
