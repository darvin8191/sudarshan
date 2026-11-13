import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Check, Loader2, Plus, Search } from 'lucide-react'
import { Fragment, useEffect, useState } from 'react'
import { watchlistError } from '@/api/watchlist'
import { cn } from '@/lib/utils'
import { keyOf } from '@/sudarshan/dashboard/live'
import { showToast } from '@/utils/toast'
import { SEARCH_DEBOUNCE_MS, SEARCH_LIMIT, SEARCH_MIN_LENGTH } from './AddSymbolSheet'
import { BottomSheet } from './BottomSheet'
import type { FollowForm } from './followRows'
import { clearRecents, pushRecent, type Recent, readRecents } from './recents'
import { CHIPS, GROUP_TITLE, runsOf, underlyingExchange } from './searchSheet'
import { MAX_ITEMS } from './useWatchlists'
import { callOp } from './v2/api'
import type { Hit, Segment } from './v2/types'

function useSettled(value: string, ms: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

const TAP =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50'
const OUTLINED = cn(TAP, 'border border-input bg-card text-foreground active:bg-accent')

/** One row: the trader's words, and Add, or Added for a symbol the list already holds. */
function Line({
  title,
  detail,
  inList,
  busy,
  disabled,
  full,
  onAdd,
}: {
  title: string
  detail: string
  inList: boolean
  busy: boolean
  disabled: boolean
  full: boolean
  onAdd: () => void
}) {
  return (
    <li>
      <button
        type="button"
        disabled={inList || full || disabled}
        onClick={onAdd}
        aria-label={inList ? `${title}, ${detail}, in the list` : `Add ${title}, ${detail}`}
        className="flex min-h-14 w-full items-center gap-3 px-4 py-2 text-left outline-none active:bg-accent focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-default"
      >
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-[0.9375rem] leading-tight font-semibold">{title}</span>
          <span className="truncate text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
            {detail}
          </span>
        </span>
        <span
          aria-hidden="true"
          className={cn(
            'flex h-9 shrink-0 items-center gap-1 rounded-full px-3 text-xs font-bold',
            inList
              ? 'bg-[color:var(--primary-tint)] text-primary'
              : full
                ? 'border border-input text-muted-foreground'
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
}

/**
 * The search sheet of v2 (M8.18, F13), which replaces today's Add sheet at `?sheet=add` while the switch
 * is on: the server's `search` op in the trader's words, the chips, the understood line, the near miss,
 * Recent for an empty field, and for an underlying with options Pick a strike (F14's seam) and the two
 * rows that follow the money (F11's form). Every request is a search the owner typed or a chip he
 * touched; nothing here asks on a clock.
 */
export function SearchSheet({
  open,
  onOpenChange,
  listId,
  listName,
  held,
  full,
  onAdd,
  onFollow,
  onPickStrike,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The list the sheet adds to; the server marks what it holds. */
  listId: number
  listName: string
  /** EXCHANGE:SYMBOL of everything already in the list. */
  held: ReadonlySet<string>
  full: boolean
  onAdd: (symbol: string, exchange: string) => Promise<void>
  /** F11's form, for Add ATM row and Add current-month FUT row. */
  onFollow: (form: FollowForm) => void
  /** F14's strike picker: while absent, Pick a strike is shown and disabled. */
  onPickStrike?: (underlying: string, exchange: string) => void
}) {
  const [text, setText] = useState('')
  const [seg, setSeg] = useState<Segment>('all')
  const [adding, setAdding] = useState<string | null>(null)
  const [recents, setRecents] = useState<Recent[]>(readRecents)
  const query = useSettled(text.trim(), SEARCH_DEBOUNCE_MS)
  const searching = open && query.length >= SEARCH_MIN_LENGTH

  const found = useQuery({
    queryKey: ['sudarshan', 'v2', 'search', listId, seg, query.toUpperCase()],
    queryFn: ({ signal }) =>
      callOp('search', { q: query, seg, limit: SEARCH_LIMIT, list: listId }, signal),
    enabled: searching,
    staleTime: 30_000,
    retry: false,
    placeholderData: keepPreviousData,
  })
  const answer = searching ? found.data : undefined
  const hits = answer?.hits ?? []

  const add = async (symbol: string, exchange: string, title: string, detail: string) => {
    setAdding(keyOf(symbol, exchange))
    try {
      await onAdd(symbol, exchange)
      setRecents((now) => pushRecent(now, { symbol, exchange, title, detail }))
    } catch (error) {
      showToast.error(watchlistError(error, `Could not add ${symbol}`))
    } finally {
      setAdding(null)
    }
  }
  const addHit = (hit: Hit) =>
    void add(hit.contract.symbol, hit.contract.exchange, hit.title, hit.detail)

  const underlying = answer?.underlying ?? null
  const base = underlying ? underlyingExchange(underlying, hits) : null
  const follow = (kind: 'atm_option' | 'month_future') =>
    underlying &&
    base &&
    onFollow({ mode: 'add', kind, underlying, exchange: base, sectionId: null })

  const empty = text.trim() === ''
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setText('')
          setSeg('all')
        }
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
          <span className="sr-only">Symbol, name or contract</span>
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
            placeholder="Symbol, name or contract"
            className="h-full min-w-0 flex-1 bg-transparent text-base font-medium outline-none placeholder:text-[color:var(--text-tertiary)]"
          />
          {found.isFetching && (
            <Loader2
              aria-hidden="true"
              className="size-5 shrink-0 animate-spin text-muted-foreground"
            />
          )}
        </label>

        <fieldset className="-mx-4 m-0 flex min-w-0 shrink-0 gap-2 overflow-x-auto border-0 px-4 py-0">
          <legend className="sr-only">Kind of symbol</legend>
          {CHIPS.map((chip) => (
            <button
              key={chip.seg}
              type="button"
              aria-pressed={seg === chip.seg}
              onClick={() => setSeg(chip.seg)}
              className={cn(
                'inline-flex min-h-12 shrink-0 items-center gap-1.5 rounded-full border px-4 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                seg === chip.seg
                  ? 'border-primary bg-[color:var(--primary-tint)] text-primary'
                  : 'border-input bg-card text-foreground'
              )}
            >
              {seg === chip.seg && <Check aria-hidden="true" className="size-4" />}
              {chip.label}
            </button>
          ))}
        </fieldset>

        {full && (
          <output className="block rounded-[0.875rem] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--gold-ink)]">
            {listName} holds {MAX_ITEMS} symbols, the most a list can. Remove one to add another.
          </output>
        )}

        <div aria-live="polite" className="sr-only">
          {answer ? `${hits.length} results` : ''}
        </div>

        {empty ? (
          recents.length === 0 ? (
            <p className="px-1 py-2 text-sm text-muted-foreground">
              Type at least {SEARCH_MIN_LENGTH} letters.
            </p>
          ) : (
            <section aria-label="Recent" className="flex min-h-0 flex-1 flex-col">
              <div className="flex shrink-0 items-center justify-between">
                <h3 className="px-1 text-sm font-bold text-[color:var(--text-secondary)]">
                  Recent
                </h3>
                <button
                  type="button"
                  onClick={() => {
                    clearRecents()
                    setRecents([])
                  }}
                  className={cn(OUTLINED, 'border-0 text-primary')}
                >
                  Clear recents
                </button>
              </div>
              <ul className="-mx-4 min-h-0 flex-1 divide-y divide-[color:var(--divider)] overflow-y-auto">
                {recents.map((r) => {
                  const key = keyOf(r.symbol, r.exchange)
                  return (
                    <Line
                      key={key}
                      title={r.title}
                      detail={r.detail}
                      inList={held.has(key)}
                      busy={adding === key}
                      disabled={adding !== null}
                      full={full}
                      onAdd={() => void add(r.symbol, r.exchange, r.title, r.detail)}
                    />
                  )
                })}
              </ul>
            </section>
          )
        ) : !searching ? (
          <p className="px-1 py-2 text-sm text-muted-foreground">
            Type at least {SEARCH_MIN_LENGTH} letters.
          </p>
        ) : found.isError ? (
          <p className="px-1 py-2 text-sm text-muted-foreground">
            {found.error instanceof Error && found.error.message
              ? found.error.message
              : 'Search is not available right now. Try again in a moment.'}
          </p>
        ) : answer ? (
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
            {answer.understood && (
              <p className="px-1 text-sm font-semibold text-[color:var(--text-secondary)]">
                {answer.understood}
              </p>
            )}
            {answer.suggestion && (
              <p className="px-1 text-sm text-muted-foreground">{answer.suggestion}</p>
            )}
            {underlying && (
              <div className="flex flex-col gap-2 px-1">
                <button
                  type="button"
                  disabled={!onPickStrike || !base}
                  onClick={() => base && onPickStrike?.(underlying, base)}
                  className={OUTLINED}
                >
                  Pick a strike
                </button>
                {base && (
                  <div className="grid grid-cols-2 gap-2">
                    <button type="button" onClick={() => follow('atm_option')} className={OUTLINED}>
                      Add ATM row
                    </button>
                    <button
                      type="button"
                      onClick={() => follow('month_future')}
                      className={OUTLINED}
                    >
                      Add current-month FUT row
                    </button>
                  </div>
                )}
              </div>
            )}
            {runsOf(hits).map((run, index) => (
              <Fragment key={`${run.group}-${index}`}>
                <h3 className="px-1 pt-1 text-sm font-bold text-[color:var(--text-secondary)]">
                  {GROUP_TITLE[run.group]}
                </h3>
                <ul className="-mx-4 divide-y divide-[color:var(--divider)]">
                  {run.hits.map((hit) => {
                    const key = keyOf(hit.contract.symbol, hit.contract.exchange)
                    return (
                      <Line
                        key={key}
                        title={hit.title}
                        detail={hit.detail}
                        inList={hit.added || held.has(key)}
                        busy={adding === key}
                        disabled={adding !== null}
                        full={full}
                        onAdd={() => addHit(hit)}
                      />
                    )
                  })}
                </ul>
              </Fragment>
            ))}
            {hits.length === 0 && !answer.suggestion && (
              <p className="px-1 py-2 text-sm text-muted-foreground">
                No symbol matches “{query}”.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </BottomSheet>
  )
}
