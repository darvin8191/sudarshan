import { Plus, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  EmptyCard,
  LoadingCards,
  OUTLINED,
  STALE,
  StaleBookNote,
} from '@/sudarshan/books/cards/parts'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { cardKey } from './dashboardCache'
import { StatusChip } from './marks'
import type { Hearing } from './push'
import { type WidgetCard, WORDS } from './types'
import { useDashboard } from './useDashboard'
import type { Address } from './useScreenerAddress'

/** The sentences the dashboard says when it has nothing to show (design 3.5). */
export const DASH_WORDS = {
  empty_title: 'Nothing on the dashboard yet',
  empty_hint:
    'Add a widget for any screen: its matches, with prices that tick, here at the top of the Screener.',
  last_read: 'These are your widgets as last read.',
} as const

/** One column on a phone, two from 768 px, three from 1,200 px. */
export const GRID = 'grid grid-cols-1 items-start gap-2 md:grid-cols-2 min-[1200px]:grid-cols-3'

/** The cards the dashboard shows. Replaced by the table card in the next change. */
function Cards({ cards }: { cards: WidgetCard[] }) {
  return (
    <ul className={GRID}>
      {cards.map((card) => (
        <li
          key={cardKey(card)}
          data-widget={cardKey(card)}
          className="flex flex-col gap-1 rounded-[1rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]"
        >
          <span className="flex items-start justify-between gap-2">
            <span className="min-w-0 truncate text-base font-bold">{card.name}</span>
            <StatusChip status={card.status} />
          </span>
          <span className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {card.where}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * The Dashboard tab (design 3.1, 3.7): the Add a widget button and the owner's widgets, one card
 * each, in the order the screener keeps them. A layout that is only a suggestion (version 0) is
 * shown like any other. A failed read says so and keeps the cards as last read, greyed.
 */
export function Dashboard({
  address,
  hearing: _hearing,
  openSheet,
  closeSheet,
}: {
  address: Address
  hearing: Hearing
  openSheet: (sheet: string, extra?: Record<string, string>) => void
  closeSheet: () => void
}) {
  const { query, retry } = useDashboard()
  const data = query.data
  const problem = query.error
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3 px-1">
        <button
          type="button"
          onClick={() => openSheet('add')}
          className="inline-flex min-h-12 items-center gap-1.5 rounded-[0.875rem] bg-primary px-4 text-[0.9375rem] font-semibold text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Plus aria-hidden="true" className="size-5" />
          Add a widget
        </button>
        {problem && (
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => {
              retry()
              void query.refetch()
            }}
            className={OUTLINED}
          >
            <RefreshCw
              aria-hidden="true"
              className={cn('size-4', query.isFetching && 'animate-spin')}
            />
            Try again
          </button>
        )}
      </div>
      {problem && (
        <p role="alert" className="px-1 text-[0.8125rem] font-semibold">
          {problem.code === 'unanswered' || problem.code === 'failed'
            ? WORDS.not_answering
            : problem.message}
        </p>
      )}
      {problem && data && <StaleBookNote text={DASH_WORDS.last_read} />}
      {!data ? (
        query.isPending ? (
          <LoadingCards />
        ) : null
      ) : data.widgets.length === 0 ? (
        <EmptyCard title={DASH_WORDS.empty_title} hint={DASH_WORDS.empty_hint} />
      ) : (
        <div className={cn(problem && STALE)}>
          <Cards cards={data.widgets} />
        </div>
      )}
      <BottomSheet
        open={address.sheet === 'add'}
        onOpenChange={(next) => !next && closeSheet()}
        title="Add a widget"
        description="Choose a screen to show on the dashboard"
      >
        <p className="text-sm font-medium text-[color:var(--text-secondary)]">
          The list of screens to add appears here.
        </p>
      </BottomSheet>
    </div>
  )
}
