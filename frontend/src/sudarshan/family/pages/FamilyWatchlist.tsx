// SUDARSHAN P6 D5 (M8.15): the lists the account owner shares with a member, read only. One
// list at a time behind chips; each row is a symbol, its last price and the day's change. There is
// no add, no reorder, no Buy or Sell, no option chain, no depth and no chart here.
//
// Prices tick on the family socket, up to the socket's cap of symbols in screen order: a row
// past it says "Not real-time · too many symbols open" instead of a price (rule 7), and a
// screen whose feed is not delivering says so in the status line, never "Real-time". The list's
// own membership is not market data: it is read when the screen opens and re-read once a minute.
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import {
  EmptyCard,
  Last,
  LoadingCards,
  ProblemCard,
  SignedPercent,
} from '@/sudarshan/books/cards/parts'
import { NotRealTime } from '@/sudarshan/books/cards/readOnlyParts'
import { LiveLine } from '@/sudarshan/dashboard/LiveLine'
import { describe } from '@/sudarshan/watchlist/rows'
import { FamilyError, familyApi, NO_ANSWER } from '../api'
import { useFamilyFeed } from '../familyManager'
import { familyMarketOpen } from '../feedLine'
import type { ListRow } from '../types'
import { useFamilyScreen } from '../useBook'
import { useViewerLive } from '../useViewerLive'

export const LISTS_LINE = 'Lists the account owner shares with you'
export const NO_LISTS = 'No list is shared with you yet. Ask the account owner.'
/** The list's own membership is re-read this often: it is not market data (rule 7). */
export const LIST_REREAD_MS = 60_000

const LISTS_KEY = ['family', 'watchlists'] as const
const listKey = (id: number) => ['family', 'watchlist', id] as const

const wordsOf = (error: unknown) => (error instanceof FamilyError ? error.message : NO_ANSWER)
const keyOf = (row: ListRow) => `${row.exchange.toUpperCase()}:${row.symbol.toUpperCase()}`

function Row({
  row,
  ltp,
  prevClose,
  tick,
  stale,
  capped,
}: {
  row: ListRow
  ltp: number | null
  prevClose: number | null
  tick: 'up' | 'down' | null
  stale: boolean
  capped: boolean
}) {
  const named = describe(row.symbol, row.exchange)
  const percent =
    ltp !== null && prevClose !== null && prevClose > 0
      ? ((ltp - prevClose) / prevClose) * 100
      : null
  return (
    <li
      data-symbol={row.symbol}
      data-stale={stale || undefined}
      className="flex min-h-14 w-full flex-col gap-1 rounded-[1rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
    >
      <span className="flex items-baseline justify-between gap-3">
        <span
          className="min-w-0 truncate text-[0.9375rem] leading-tight font-semibold"
          title={row.symbol}
        >
          {named.title}
        </span>
        {!capped && (
          <Last
            value={ltp}
            tick={tick}
            stale={stale}
            exchange={row.exchange}
            label={null}
            className="text-base"
          />
        )}
      </span>
      <span className="flex items-baseline justify-between gap-3 text-xs leading-tight font-medium text-[color:var(--text-tertiary)]">
        <span className="min-w-0 truncate">
          {row.name && row.name !== row.symbol ? `${row.name} · ` : ''}
          {named.detail}
        </span>
        {!capped && <SignedPercent value={percent} stale={stale} className="font-semibold" />}
      </span>
      {capped && <NotRealTime readAt={null} />}
    </li>
  )
}

export default function FamilyWatchlist() {
  useFamilyScreen('watchlist')
  const feed = useFamilyFeed().state
  const marketOpen = useMemo(() => familyMarketOpen(feed), [feed])
  const lists = useQuery({
    queryKey: LISTS_KEY,
    queryFn: () => familyApi.watchlists(),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: LIST_REREAD_MS,
  })
  const [chosen, setChosen] = useState<number | null>(null)
  const available = lists.data ?? []
  // A list the owner stopped sharing falls back to the first one that is left.
  const selected = available.find((each) => each.id === chosen) ?? available[0] ?? null
  const view = useQuery({
    queryKey: listKey(selected?.id ?? -1),
    queryFn: () => familyApi.watchlist(selected?.id ?? -1),
    enabled: selected !== null,
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchInterval: LIST_REREAD_MS,
  })
  const rows = useMemo(() => view.data?.rows ?? [], [view.data])
  const instruments = useMemo(
    () => rows.map((row) => ({ symbol: row.symbol, exchange: row.exchange })),
    [rows]
  )
  const live = useViewerLive({ instruments, marketOpen })

  let content
  if (lists.isPending) {
    content = <LoadingCards />
  } else if (lists.error && available.length === 0) {
    content = <ProblemCard message={wordsOf(lists.error)} onRetry={() => void lists.refetch()} />
  } else if (selected === null) {
    content = <EmptyCard title={NO_LISTS} />
  } else if (view.isPending) {
    content = <LoadingCards />
  } else if (view.error && rows.length === 0) {
    content = <ProblemCard message={wordsOf(view.error)} onRetry={() => void view.refetch()} />
  } else if (rows.length === 0) {
    content = (
      <EmptyCard title="This list is empty" hint="The account owner has not added a symbol." />
    )
  } else {
    content = (
      <ul aria-label={selected.name} className="flex flex-col gap-2">
        {rows.map((row) => {
          const tick = live.ticks.get(keyOf(row))
          return (
            <Row
              key={keyOf(row)}
              row={row}
              ltp={tick?.ltp ?? null}
              prevClose={tick?.prev_close ?? null}
              tick={live.directions.get(keyOf(row)) ?? null}
              stale={live.stale({ symbol: row.symbol, exchange: row.exchange })}
              capped={live.capped.has(keyOf(row))}
            />
          )
        })}
      </ul>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="px-1 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
        {LISTS_LINE}
      </p>
      {available.length > 1 && (
        <fieldset className="-mx-4 min-w-0">
          <legend className="sr-only">Lists shared with you</legend>
          <div className="flex gap-1.5 overflow-x-auto px-4 [scrollbar-width:none]">
            {available.map((each) => {
              const on = each.id === selected?.id
              return (
                <button
                  key={each.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setChosen(each.id)}
                  className="group flex min-h-12 shrink-0 items-center outline-none"
                >
                  <span
                    className={cn(
                      'flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm group-focus-visible:ring-2 group-focus-visible:ring-ring',
                      on
                        ? 'border-primary bg-[color:var(--primary-tint)] font-bold text-primary'
                        : 'border-input bg-card font-semibold text-[color:var(--text-secondary)]'
                    )}
                  >
                    {each.name}
                    <span className="tabular-nums">{each.count}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </fieldset>
      )}
      {selected !== null && rows.length > 0 && (
        <p className="flex min-h-5 items-center px-1">
          <LiveLine line={live.line} now={live.now} />
        </p>
      )}
      {content}
    </div>
  )
}
