// SUDARSHAN P6 D6 (M7.41, M7.37): the member's view of the portfolios the owner shared. The
// "Shared portfolios" summary first, then one tab at a time. No New tab, Add, Import, Customize,
// Tab settings or Delete exist here. Nothing on this screen is real-time: it is a read, stamped
// with the time the service took it, and re-read when the service says it has (Y11), on return to
// the page and every 45 s while the market is open. A tab shared as restricted shows holdings and allocation by count only.
import { useQuery } from '@tanstack/react-query'
import { Check, Lock } from 'lucide-react'
import { useSearchParams } from 'react-router'
import { cn } from '@/lib/utils'
import { Hidden } from '@/sudarshan/books/cards/Hidden'
import { ProblemCard } from '@/sudarshan/books/cards/parts'
import {
  Figure,
  HiddenTotal,
  Plain,
  ReadStamp,
  ServiceWords,
} from '@/sudarshan/books/cards/readOnlyParts'
import { RESYNC_MS } from '@/sudarshan/dashboard/live'
import { FamilyError, familyApi, NO_ANSWER } from '../api'
import { useFamilyFeed } from '../familyManager'
import { allocationOf, holdingsOf, summaryLines, sumOf, totalsOf } from '../portfolioView'
import type { SharedTab } from '../types'
import { useFamilyScreen } from '../useBook'

const CARD =
  'flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const NOT_SHARED = 'Not shared with you.'

const wordsOf = (error: unknown) => (error instanceof FamilyError ? error.message : NO_ANSWER)

/** A percentage is the service's own text. Worth is drawn plain; only a gain carries a sign. */
function Share({ pct }: { pct: string | null | undefined }) {
  if (pct === undefined) return null
  return (
    <span className="text-xs font-semibold text-muted-foreground">
      {pct === null ? '-' : `${pct}%`}
    </span>
  )
}

function Summary() {
  // Review L4 (v0.12.0): no timed read while the market is closed, as a tab's own read does.
  const closed = useFamilyFeed().state === 'closed'
  const query = useQuery({
    queryKey: ['family', 'portfolio'],
    queryFn: ({ signal }) => familyApi.portfolio(signal),
    retry: false,
    refetchOnMount: 'always',
    staleTime: 0,
    refetchInterval: closed ? false : RESYNC_MS,
  })
  const data = query.data
  const lines = summaryLines(data?.summary)
  const value = sumOf(lines, (f) => f.value)
  const today = sumOf(lines, (f) => f.today)
  return (
    <section aria-labelledby="sd-family-shared" className={CARD}>
      <h2 id="sd-family-shared" className="text-[0.84375rem] font-semibold text-muted-foreground">
        Shared portfolios
      </h2>
      {data && <ServiceWords state={data.state} words={data.words} />}
      {query.isPending ? (
        <p className="text-sm text-muted-foreground">Reading the portfolios</p>
      ) : query.error && !data ? (
        <ProblemCard message={wordsOf(query.error)} onRetry={() => void query.refetch()} />
      ) : lines.length === 0 ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {data?.state === 'not_shared' ? NOT_SHARED : 'No shared portfolio figures to show yet.'}
        </p>
      ) : (
        <>
          <p data-testid="shared-value" className="sd-display text-[2rem] leading-[1.05] font-bold">
            {value === undefined ? <HiddenTotal /> : <Plain value={value} />}
          </p>
          <p className="flex items-baseline gap-1.5 text-sm font-semibold">
            <span className="text-muted-foreground">Today</span>
            <span data-testid="shared-today">
              {today === undefined ? <Hidden /> : <Figure value={today} />}
            </span>
          </p>
          <ul className="flex flex-col gap-1 border-t border-[color:var(--divider)] pt-2">
            {lines.map((line) => (
              <li
                key={`${line.id}:${line.name}`}
                className="flex items-baseline justify-between gap-3"
              >
                <span className="min-w-0 truncate text-sm font-medium">{line.name}</span>
                <span className="text-sm font-semibold">
                  <Plain value={line.figures.value} />
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <ReadStamp part="Portfolios" readAt={data?.read_at ?? null} closed={closed} />
    </section>
  )
}

function TabBody({ tab }: { tab: SharedTab }) {
  const feed = useFamilyFeed().state
  const query = useQuery({
    queryKey: ['family', 'portfolio', 'tab', tab.id],
    queryFn: ({ signal }) => familyApi.portfolioTab(tab.id, signal),
    retry: false,
    refetchOnMount: 'always',
    staleTime: 0,
    refetchInterval: feed === 'closed' ? false : RESYNC_MS,
  })
  const data = query.data
  const restricted = tab.level === 'restricted'
  const figures = totalsOf(data?.view)
  const holdings = holdingsOf(data?.view)
  const allocation = allocationOf(data?.view)
  return (
    <section aria-label={tab.name} className="flex flex-col gap-3">
      {restricted && (
        <p className="flex items-center gap-2 rounded-[1rem] border bg-card px-3 py-2.5 text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">
          <Lock aria-hidden="true" className="size-4 shrink-0" />
          Shared with limited detail: holdings and how many sit in each group.
        </p>
      )}
      {data && <ServiceWords state={data.state} words={data.words} />}
      {query.isPending ? (
        <p className="text-sm text-muted-foreground">Reading the portfolio</p>
      ) : query.error && !data ? (
        <ProblemCard message={wordsOf(query.error)} onRetry={() => void query.refetch()} />
      ) : data?.state === 'not_shared' ? (
        <p className="text-sm text-[color:var(--text-secondary)]">{NOT_SHARED}</p>
      ) : (
        <>
          {!restricted && (
            <dl className={cn(CARD, 'grid grid-cols-2 gap-x-3 gap-y-2')}>
              <div className="flex flex-col gap-px">
                <dt className="text-xs font-semibold text-muted-foreground">Value</dt>
                <dd data-testid="tab-value" className="text-[0.9375rem] font-bold">
                  <Plain value={figures.value} />
                </dd>
              </div>
              <div className="flex flex-col gap-px">
                <dt className="text-xs font-semibold text-muted-foreground">Overall gain</dt>
                <dd data-testid="tab-gain" className="text-[0.9375rem] font-bold">
                  <Figure value={figures.gain} />
                </dd>
              </div>
              <div className="col-span-2 flex flex-col gap-px">
                <dt className="text-xs font-semibold text-muted-foreground">Today</dt>
                <dd className="flex items-baseline gap-1.5 text-[0.9375rem] font-bold">
                  <Figure value={figures.today} />
                  <Share pct={figures.todayPct} />
                </dd>
              </div>
            </dl>
          )}
          {allocation.length > 0 && (
            <ul aria-label="Allocation" className={CARD}>
              {allocation.map((row) => (
                <li key={row.label} className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium">{row.label}</span>
                  <span className="flex items-baseline gap-2 whitespace-nowrap">
                    {restricted ? (
                      <span className="font-semibold">
                        {row.holdings === 1 ? '1 holding' : `${row.holdings} holdings`}
                      </span>
                    ) : (
                      <>
                        <Share pct={row.pct} />
                        <span className="font-semibold">
                          <Plain value={row.value} />
                        </span>
                      </>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {holdings.length > 0 && (
            <ul aria-label="Holdings" className={cn(CARD, 'gap-1')}>
              {holdings.map((holding) => (
                <li key={holding.key} className="flex min-h-12 flex-col justify-center">
                  <span className="text-sm font-semibold">{holding.name}</span>
                  {holding.symbol && holding.symbol !== holding.name && (
                    <span className="text-xs text-muted-foreground">{holding.symbol}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <ReadStamp part="Portfolio" readAt={data?.read_at ?? null} closed={feed === 'closed'} />
    </section>
  )
}

export default function FamilyPortfolio() {
  useFamilyScreen('portfolio')
  const [params, setParams] = useSearchParams()
  const closed = useFamilyFeed().state === 'closed'
  const list = useQuery({
    queryKey: ['family', 'portfolio'],
    queryFn: ({ signal }) => familyApi.portfolio(signal),
    retry: false,
    refetchOnMount: 'always',
    staleTime: 0,
    refetchInterval: closed ? false : RESYNC_MS,
  })
  const tabs = list.data?.tabs ?? []
  const wanted = Number(params.get('tab'))
  const current = tabs.find((tab) => tab.id === wanted) ?? tabs[0] ?? null

  return (
    <div className="flex flex-col gap-3">
      <Summary />
      {tabs.length > 0 && (
        <div
          role="tablist"
          aria-label="Shared portfolios"
          className="flex gap-2 overflow-x-auto pb-1"
        >
          {tabs.map((tab) => {
            const selected = current?.id === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setParams({ tab: String(tab.id) }, { replace: true })}
                className={cn(
                  'inline-flex min-h-12 shrink-0 items-center gap-1.5 rounded-xl border px-4 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  // never colour alone (rule 6): the selected tab is bold and carries a check
                  selected
                    ? 'border-[color:var(--family)] bg-accent font-bold'
                    : 'bg-card font-semibold hover:bg-accent'
                )}
              >
                {selected && <Check aria-hidden="true" data-mark="selected" className="size-3.5" />}
                {tab.level === 'restricted' && <Lock aria-hidden="true" className="size-3.5" />}
                {tab.name}
                {tab.level === 'restricted' && <span className="sr-only"> limited detail</span>}
              </button>
            )
          })}
        </div>
      )}
      {current && <TabBody key={current.id} tab={current} />}
      {!list.isPending && !list.error && tabs.length === 0 && (
        <p className="rounded-[1.125rem] border bg-card px-4 py-5 text-sm text-[color:var(--text-secondary)]">
          {list.data?.words ?? 'Nothing is shared with you here yet.'}
        </p>
      )}
      {list.error && !list.data && (
        <ProblemCard message={wordsOf(list.error)} onRetry={() => void list.refetch()} />
      )}
    </div>
  )
}
