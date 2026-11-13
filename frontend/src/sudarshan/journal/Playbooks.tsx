// SUDARSHAN M14.5, JD5: the Playbooks part. Each setup the owner trades as a card with how it has
// done (trades, win rate, expectancy: the analytics' own block, over every closed trade of this
// mode), and a sheet to create or change one. Re-read as the other parts are (useJournalSync).
import { useQuery } from '@tanstack/react-query'
import { Archive, BookMarked, LoaderCircle, Plus, RefreshCw, TriangleAlert } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { ratioWords } from './analyticsWords'
import { journalData, problemOf } from './api'
import { JOURNAL_KEY } from './keys'
import { paiseAmount } from './money'
import type { BlockRow, Mode, Playbook } from './types'
import { useJournalSync } from './useJournalSync'

const CARD = 'rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)]'

export const EMPTY_WORDS =
  'No playbooks yet. A playbook is a setup you trade, with the checks you want to see before you enter. Add one to see how it does.'

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 rounded-[0.75rem] bg-[color:var(--bg-canvas)] px-2.5 py-2">
      <span className="text-[0.6875rem] font-semibold text-[color:var(--text-secondary)]">
        {label}
      </span>
      <span className="text-[0.9375rem] leading-tight font-bold">{children}</span>
    </div>
  )
}

function PlaybookCard({
  playbook,
  stats,
  onOpen,
}: {
  playbook: Playbook
  stats: BlockRow | undefined
  onOpen: (id: number) => void
}) {
  const m = stats?.metrics
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(playbook.id)}
        data-testid="playbook"
        className={cn(
          CARD,
          'flex min-h-12 w-full flex-col gap-2 px-4 py-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'
        )}
      >
        <span className="flex items-baseline justify-between gap-2">
          <span className="min-w-0 truncate text-base font-bold">{playbook.name}</span>
          {playbook.name_alt && (
            <span className="min-w-0 truncate text-sm font-medium text-[color:var(--text-secondary)]">
              {playbook.name_alt}
            </span>
          )}
        </span>
        {playbook.description && (
          <span className="line-clamp-2 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
            {playbook.description}
          </span>
        )}
        <span className="text-xs font-medium text-[color:var(--text-tertiary)]">
          {playbook.rules.length} {playbook.rules.length === 1 ? 'check' : 'checks'}
          {playbook.default_risk_paise !== null &&
            `, default risk ${paiseAmount(playbook.default_risk_paise)}`}
        </span>
        <span className="grid grid-cols-3 gap-2">
          <Stat label="Trades">{m ? m.trips : 0}</Stat>
          <Stat label="Win rate">
            {m ? ratioWords(m.win_rate, m.win_rate_state, '%') : 'No trades'}
          </Stat>
          <Stat label="Expectancy">
            {m && m.expectancy_paise !== null ? <Signed paise={m.expectancy_paise} /> : '-'}
          </Stat>
        </span>
      </button>
    </li>
  )
}

/** The playbooks and how each has done, for one mode. */
export function usePlaybooks(mode: Mode) {
  const list = useQuery({
    queryKey: [...JOURNAL_KEY, 'playbooks'],
    queryFn: async () => journalData('playbooks_list'),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  const stats = useQuery({
    queryKey: [...JOURNAL_KEY, 'playbook-stats', mode],
    queryFn: async () => journalData('analytics', { mode, period: 'all' }),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  })
  return { list, stats }
}

export function Playbooks({
  mode,
  onOpen,
  onNew,
}: {
  mode: Mode
  onOpen: (id: number) => void
  onNew: () => void
}) {
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { anyOpen } = useMarketOpen(market)
  const { list, stats } = usePlaybooks(mode)
  const { refetch: reread } = list
  const { refetch: rereadStats } = stats
  const read = useCallback(() => Promise.all([reread(), rereadStats()]), [reread, rereadStats])
  useJournalSync(read, { marketOpen: anyOpen })
  const byName = useMemo(
    () => new Map((stats.data?.by_playbook ?? []).map((row) => [row.name, row])),
    [stats.data]
  )
  const active = (list.data ?? []).filter((p) => !p.archived)
  const archived = (list.data ?? []).filter((p) => p.archived)

  let body: React.ReactNode
  if (list.isPending) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')} aria-live="polite">
        <LoaderCircle aria-hidden="true" className="mx-auto mb-2 size-6 animate-spin" />
        <p className="text-base font-bold">Loading your playbooks</p>
      </div>
    )
  } else if (list.isError && !list.data) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')} aria-live="polite">
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{problemOf(list.error).message}</p>
        <button
          type="button"
          onClick={() => list.refetch()}
          className="mt-3 inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else if (active.length === 0 && archived.length === 0) {
    body = (
      <div className={cn(CARD, 'px-4 py-6 text-center')}>
        <BookMarked
          aria-hidden="true"
          className="mx-auto mb-2 size-6 text-[color:var(--text-secondary)]"
        />
        <p className="mx-auto max-w-sm text-sm font-medium text-[color:var(--text-secondary)]">
          {EMPTY_WORDS}
        </p>
      </div>
    )
  } else {
    body = (
      <>
        {stats.isError && (
          <p
            role="alert"
            className="rounded-[0.875rem] border border-[color:var(--border-strong)] bg-card px-3 py-2 text-sm font-medium text-[color:var(--text-secondary)]"
          >
            {problemOf(stats.error).message} The results below may be missing.
          </p>
        )}
        <ul className="flex flex-col gap-2" aria-label="Playbooks">
          {active.map((p) => (
            <PlaybookCard key={p.id} playbook={p} stats={byName.get(p.name)} onOpen={onOpen} />
          ))}
        </ul>
        {archived.length > 0 && (
          <section aria-label="Archived playbooks" className="flex flex-col gap-2">
            <h2 className="flex items-center gap-1.5 px-1 text-[0.9375rem] font-bold">
              <Archive aria-hidden="true" className="size-4" />
              Archived
            </h2>
            <ul className="flex flex-col gap-2">
              {archived.map((p) => (
                <PlaybookCard key={p.id} playbook={p} stats={byName.get(p.name)} onOpen={onOpen} />
              ))}
            </ul>
          </section>
        )}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      {list.isSuccess && (
        <button
          type="button"
          onClick={onNew}
          className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] bg-[color:var(--brand)] px-4 text-[0.9375rem] font-bold text-white outline-none hover:bg-[color:var(--brand-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <Plus aria-hidden="true" className="size-4" />
          New playbook
        </button>
      )}
      {body}
    </div>
  )
}
