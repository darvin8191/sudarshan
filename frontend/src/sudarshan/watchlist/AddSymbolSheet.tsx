import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, Plus, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { watchlistError } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { keyOf } from '@/sudarshan/dashboard/live'
import { showToast } from '@/utils/toast'
import { type SearchResult, searchSymbols } from './api'
import { BottomSheet } from './BottomSheet'
import { describe } from './rows'

/** One letter matches half the exchange; two is where a search starts to mean something. */
export const SEARCH_MIN_LENGTH = 2

/** How long typing has to pause before the search goes out. */
export const SEARCH_DEBOUNCE_MS = 250

/** What a phone list can usefully show; the server has already put the closest first. */
export const SEARCH_LIMIT = 50

function useSettled(value: string, ms: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

/**
 * Add symbols to the open list: the app's instrument search (`/api/v1/search`,
 * the master contract the /trading terminal searches too), several words
 * allowed ("NIFTY 25000 CE"). The sheet stays open, so several symbols can go
 * in one after another; one already in the list says so instead of adding it
 * twice.
 */
export function AddSymbolSheet({
  open,
  onOpenChange,
  apiKey,
  listName,
  held,
  onAdd,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  apiKey: string | null
  listName: string
  /** EXCHANGE:SYMBOL of everything already in the list. */
  held: ReadonlySet<string>
  onAdd: (result: SearchResult) => Promise<void>
}) {
  const [text, setText] = useState('')
  const [adding, setAdding] = useState<string | null>(null)
  const query = useSettled(text.trim(), SEARCH_DEBOUNCE_MS)
  const searching = open && apiKey !== null && query.length >= SEARCH_MIN_LENGTH

  const results = useQuery({
    queryKey: ['sudarshan', 'symbol-search', query.toUpperCase()],
    queryFn: () => searchSymbols(apiKey ?? '', query),
    enabled: searching,
    staleTime: 5 * 60_000,
    retry: false,
  })
  const rows = (results.data ?? []).slice(0, SEARCH_LIMIT)

  const add = async (result: SearchResult) => {
    const key = keyOf(result.symbol, result.exchange)
    setAdding(key)
    try {
      await onAdd(result)
    } catch (error) {
      showToast.error(watchlistError(error, `Could not add ${result.symbol}`))
    } finally {
      setAdding(null)
    }
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) setText('')
        onOpenChange(next)
      }}
      title={`Add to ${listName}`}
      description="For example RELIANCE, or NIFTY 25000 CE"
      className="h-[92dvh]"
    >
      <div className="flex h-full flex-col gap-2">
        <label className="flex h-12 shrink-0 items-center gap-2 rounded-[0.875rem] border border-input bg-card px-3 focus-within:ring-[3px] focus-within:ring-ring/50">
          <Search
            aria-hidden="true"
            className="size-5 shrink-0 text-[color:var(--text-tertiary)]"
          />
          <span className="sr-only">Search symbols</span>
          <input
            // biome-ignore lint/a11y/noAutofocus: the sheet exists to type into; the keyboard comes up with it
            autoFocus
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="characters"
            spellCheck={false}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Symbol or name"
            className="h-full min-w-0 flex-1 bg-transparent text-base font-medium outline-none placeholder:text-[color:var(--text-tertiary)]"
          />
          {results.isFetching && (
            <Loader2
              aria-hidden="true"
              className="size-5 shrink-0 animate-spin text-muted-foreground"
            />
          )}
        </label>

        <div aria-live="polite" className="sr-only">
          {searching && results.isSuccess ? `${rows.length} results` : ''}
        </div>

        {!searching ? (
          <p className="px-1 py-2 text-sm text-muted-foreground">
            Type at least {SEARCH_MIN_LENGTH} letters.
          </p>
        ) : results.isError ? (
          <p className="px-1 py-2 text-sm text-muted-foreground">
            Search is not available right now. Try again in a moment.
          </p>
        ) : results.isSuccess && rows.length === 0 ? (
          <p className="px-1 py-2 text-sm text-muted-foreground">No symbol matches “{query}”.</p>
        ) : (
          <ul className="-mx-4 min-h-0 flex-1 divide-y divide-[color:var(--divider)] overflow-y-auto">
            {rows.map((result) => {
              const key = keyOf(result.symbol, result.exchange)
              const named = describe(result.symbol, result.exchange)
              const inList = held.has(key)
              const busy = adding === key
              const name = result.name && result.name !== result.symbol ? result.name : null
              return (
                <li key={key}>
                  <button
                    type="button"
                    disabled={inList || adding !== null}
                    onClick={() => void add(result)}
                    aria-label={
                      inList
                        ? `${named.title}, ${named.detail}, in the list`
                        : `Add ${named.title}, ${named.detail}`
                    }
                    className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-default"
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate text-[0.9375rem] leading-tight font-semibold">
                        {named.title}
                      </span>
                      <span className="truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
                        {named.detail}
                        {name ? ` · ${name}` : ''}
                      </span>
                    </span>
                    <span
                      aria-hidden="true"
                      className={cn(
                        'flex h-9 shrink-0 items-center gap-1 rounded-full px-3 text-xs font-bold',
                        inList
                          ? 'bg-[color:var(--live-tint)] text-[color:var(--live-ink)]'
                          : 'border border-primary text-primary'
                      )}
                    >
                      {busy ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : inList ? (
                        <Check className="size-4" />
                      ) : (
                        <Plus className="size-4" />
                      )}
                      {inList ? 'Added' : 'Add'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </BottomSheet>
  )
}
