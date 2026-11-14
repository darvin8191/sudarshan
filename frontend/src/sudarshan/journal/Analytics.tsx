// SUDARSHAN M14.3, JD4: the Analytics part. The filter row, six tiles, the equity curve with its
// drawdown, the calendar, the heat map, hold-time bars, results by strategy, the expiry split and
// what charges took. Every figure carries its sign and a word; a ratio with no number says why.
// Nothing here is market data: the answer is re-read as the Trades part is (useJournalSync).
import { useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useCallback, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { cn } from '@/lib/utils'
import { directionOf } from '@/sudarshan/dashboard/format'
import { useMarketOpen } from '@/sudarshan/dashboard/hooks'
import {
  CALENDAR_LOADING,
  type MarketCalendar,
  MarketCalendarSource,
  useIstDay,
} from '@/sudarshan/dashboard/MarketCalendar'
import { TREND_TEXT, TrendArrow } from '@/sudarshan/dashboard/Trend'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { ratioWords, rWords, tileRupees } from './analyticsWords'
import { journalData, problemOf, WORDS } from './api'
import { CalendarGrid } from './CalendarGrid'
import { EquityChart } from './EquityChart'
import {
  type AnalyticsFilter,
  dayToField,
  EXPIRY_CHOICES,
  EXPIRY_WORDS,
  type ExpiryChoice,
  fieldToDay,
  filterOf,
  narrowedBy,
  PERIODS,
  requestOf,
  withFilter,
} from './filters'
import { HeatMap } from './HeatMap'
import { JOURNAL_KEY } from './keys'
import { Problem } from './parts'
import {
  type Analytics as AnalyticsAnswer,
  type BlockRow,
  HOLD_BUCKET_LABELS,
  type Metrics,
  type Mode,
  PAISE_PER_RUPEE,
} from './types'
import { useJournalSync } from './useJournalSync'

const SECTION = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const FIELD =
  'min-h-12 w-full rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** The filter in the address, and how to change it (replacing the entry: Back never walks filters). */
export function useAnalyticsFilter(): [AnalyticsFilter, (next: AnalyticsFilter) => void] {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const filter = useMemo(() => filterOf(params), [params])
  const here = useRef(params)
  here.current = params
  const set = useCallback(
    (next: AnalyticsFilter) => {
      const search = withFilter(here.current, next).toString()
      navigate({ search: search ? `?${search}` : '' }, { replace: true })
    },
    [navigate]
  )
  return [filter, set]
}

function FilterRow({
  filter,
  onChange,
  strategies,
  playbooks,
  tags,
}: {
  filter: AnalyticsFilter
  onChange: (next: AnalyticsFilter) => void
  strategies: readonly string[]
  playbooks: ReadonlyArray<{ id: number; name: string }>
  tags: ReadonlyArray<{ id: number; name: string }>
}) {
  const narrowed = narrowedBy(filter)
  return (
    <section aria-label="Filters" className="flex flex-col gap-2">
      {/* Review M4: two rows of three on a phone, so Custom is never off the edge. */}
      <div
        role="tablist"
        aria-label="Period"
        className="grid grid-cols-3 gap-1 rounded-[0.875rem] border bg-card p-1 shadow-[var(--shadow-card)] sm:flex"
      >
        {PERIODS.map((p) => {
          const on = filter.period === p.id
          return (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => onChange({ ...filter, period: p.id })}
              className={cn(
                'min-h-12 min-w-14 flex-1 rounded-[0.625rem] px-3 text-[0.9375rem] font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                on
                  ? 'bg-[color:var(--brand)] font-bold text-white'
                  : 'text-[color:var(--text-secondary)] hover:bg-accent'
              )}
            >
              {p.label}
            </button>
          )
        })}
      </div>
      {filter.period === 'custom' && (
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            From
            <input
              type="date"
              value={dayToField(filter.from)}
              onChange={(e) => onChange({ ...filter, from: fieldToDay(e.target.value) })}
              className={FIELD}
            />
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
            To
            <input
              type="date"
              value={dayToField(filter.to)}
              onChange={(e) => onChange({ ...filter, to: fieldToDay(e.target.value) })}
              className={FIELD}
            />
          </label>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          Strategy
          <select
            value={filter.strategy}
            onChange={(e) => onChange({ ...filter, strategy: e.target.value })}
            className={FIELD}
          >
            <option value="">Every strategy</option>
            {strategies.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          Playbook
          <select
            value={filter.playbook ?? ''}
            onChange={(e) =>
              onChange({
                ...filter,
                playbook: e.target.value === '' ? null : Number(e.target.value),
              })
            }
            className={FIELD}
          >
            <option value="">Every playbook</option>
            {playbooks.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          Tag
          <select
            value={filter.tag ?? ''}
            onChange={(e) =>
              onChange({ ...filter, tag: e.target.value === '' ? null : Number(e.target.value) })
            }
            className={FIELD}
          >
            <option value="">Every tag</option>
            {tags.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
          Expiry
          <select
            value={filter.expiry}
            onChange={(e) => onChange({ ...filter, expiry: e.target.value as ExpiryChoice })}
            className={FIELD}
          >
            {EXPIRY_CHOICES.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {filter.expiry !== '' && (
        <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">{EXPIRY_WORDS}</p>
      )}
      {narrowed > 0 && (
        <button
          type="button"
          onClick={() =>
            onChange({ ...filter, strategy: '', playbook: null, tag: null, expiry: '' })
          }
          className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          Clear {narrowed} {narrowed === 1 ? 'filter' : 'filters'}
        </button>
      )}
    </section>
  )
}

/**
 * The size of a tile's figure by its length (review M4): a lakh or a crore keeps its whole figure
 * on one line in half a phone's width, a step smaller, rather than breaking its sign away.
 */
export function figureSize(text: string): string {
  if (text.length >= 12) return 'text-[1.125rem]'
  if (text.length >= 10) return 'text-[1.375rem]'
  return 'text-[1.625rem]'
}

function Tile({
  label,
  note,
  words = false,
  size = 'text-[1.625rem]',
  children,
}: {
  label: string
  note?: string
  /** Words in place of a figure: Barlow, which reads lighter than the display face. */
  words?: boolean
  size?: string
  children: React.ReactNode
}) {
  return (
    <div
      data-testid="tile"
      className="flex min-h-[5.5rem] min-w-0 flex-col justify-between gap-1 rounded-[1.125rem] border bg-card px-3.5 py-3 shadow-[var(--shadow-card)]"
    >
      <span className="text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
        {label}
      </span>
      {words ? (
        <span className="text-base leading-tight font-bold">{children}</span>
      ) : (
        <span className={cn('sd-display leading-none font-semibold whitespace-nowrap', size)}>
          {children}
        </span>
      )}
      {note && (
        <span className="text-[0.75rem] font-medium text-[color:var(--text-tertiary)]">{note}</span>
      )}
    </div>
  )
}

/** A signed rupee figure in a tile: sign in the text, arrow beside it, never colour alone. */
function SignedFigure({ paise, word }: { paise: number; word?: boolean }) {
  const dir = directionOf(paise / PAISE_PER_RUPEE)
  return (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap', TREND_TEXT[dir])}>
      <TrendArrow direction={dir} className="size-3" />
      {tileRupees(paise)}
      {word && (
        <span className="sr-only">
          {dir === 'up' ? 'Profit' : dir === 'down' ? 'Loss' : 'Flat'}
        </span>
      )}
    </span>
  )
}

function Tiles({ answer }: { answer: AnalyticsAnswer }) {
  const m = answer.metrics
  // Review M6: a part missing from the answer leaves its own figure out, not the page.
  const dd = answer.equity?.drawdown ?? null
  const r = rWords(m)
  return (
    <section aria-label="Results" className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        <Tile label="Net" note={netNote(m)} size={figureSize(tileRupees(m.net_paise))}>
          <SignedFigure paise={m.net_paise} word />
        </Tile>
        <Tile
          label="Win rate"
          note={`${m.wins} Profit, ${m.losses} Loss, ${m.flats} Flat`}
          words={m.win_rate_state !== 'ok'}
        >
          {ratioWords(m.win_rate, m.win_rate_state, '%')}
        </Tile>
        <Tile label="Profit factor" words={m.profit_factor_state !== 'ok'}>
          {ratioWords(m.profit_factor, m.profit_factor_state)}
        </Tile>
        <Tile
          label="Expectancy"
          note="per trade"
          size={figureSize(m.expectancy_paise === null ? '-' : tileRupees(m.expectancy_paise))}
        >
          {m.expectancy_paise === null ? '-' : <SignedFigure paise={m.expectancy_paise} />}
        </Tile>
        <Tile label="Average R" note={r.note} words={m.r_avg === null || m.r_known === 0}>
          {r.figure}
        </Tile>
        <Tile
          label="Max drawdown"
          note={
            dd && dd.max_drawdown_pct !== null && dd.max_drawdown_paise !== 0
              ? `${dd.max_drawdown_pct.replace('-', '−')}% of capital`
              : undefined
          }
          size={figureSize(dd ? tileRupees(dd.max_drawdown_paise) : '-')}
        >
          {!dd ? (
            '-'
          ) : dd.max_drawdown_paise === 0 ? (
            'None'
          ) : (
            <SignedFigure paise={dd.max_drawdown_paise} />
          )}
        </Tile>
      </div>
      {/* Review M4: one column on a phone, so a figure never runs under the next label. */}
      <dl
        data-testid="averages"
        className={`${SECTION} grid grid-cols-1 gap-x-4 gap-y-1.5 text-[0.9375rem] sm:grid-cols-2`}
      >
        {[
          ['Average win', m.avg_win_paise],
          ['Average loss', m.avg_loss_paise],
          ['Largest win', m.largest_win_paise],
          ['Largest loss', m.largest_loss_paise],
        ].map(([label, value]) => (
          <div key={label as string} className="flex items-baseline justify-between gap-2">
            <dt className="font-medium text-[color:var(--text-secondary)]">{label}</dt>
            <dd className="font-semibold">
              {value === null ? '-' : <Signed paise={value as number} />}
            </dd>
          </div>
        ))}
        <div className="flex items-baseline justify-between gap-2 sm:col-span-2">
          <dt className="font-medium text-[color:var(--text-secondary)]">
            Payoff (average win over average loss)
          </dt>
          <dd className="font-semibold">{ratioWords(m.payoff, m.payoff_state)}</dd>
        </div>
      </dl>
    </section>
  )
}

function HoldBars({ rows }: { rows: AnalyticsAnswer['hold'] }) {
  const biggest = Math.max(1, ...rows.map((r) => r.trips))
  return (
    <section aria-label="Hold time" className={`${SECTION} flex flex-col gap-2`}>
      <h2 className="text-base font-bold">How long you hold</h2>
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <li key={row.bucket} data-testid="hold-row" className="flex flex-col gap-1">
            <span className="flex items-baseline justify-between gap-2 text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]">
              {HOLD_BUCKET_LABELS[row.bucket] ?? '-'}
              <span className="inline-flex items-baseline gap-2 text-foreground">
                {row.trips} {row.trips === 1 ? 'trade' : 'trades'}
                {row.trips > 0 && <Signed paise={row.net_paise} />}
              </span>
            </span>
            <span className="block h-3 rounded-full bg-[color:var(--divider)]">
              <span
                className="block h-3 rounded-full bg-[color:var(--brand)]"
                style={{ width: `${(row.trips / biggest) * 100}%` }}
              />
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * Results by name (a strategy, a tag, a kind of day) in two columns, so a phone 360 px wide holds
 * them whole (review B1): the name, with its trades and win rate under it, takes what room is left
 * and wraps; the net keeps its full figure on one line and never scrolls out of sight.
 */
function ResultTable({
  label,
  first,
  rows,
  unnamed,
}: {
  label: string
  first: string
  rows: ReadonlyArray<{ name: string; metrics: Metrics }>
  /** What a row with no name reads. */
  unnamed: string
}) {
  return (
    <table aria-label={label} className="w-full text-left text-[0.8125rem]">
      <thead className="text-[color:var(--text-secondary)]">
        <tr>
          <th scope="col" className="py-1 pr-3 font-semibold">
            {first}
          </th>
          <th scope="col" className="py-1 text-right font-semibold whitespace-nowrap">
            Net
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map(({ name, metrics: m }) => (
          <tr key={name} className="border-t border-[color:var(--divider)] align-top">
            <th scope="row" className="py-2 pr-3 text-left font-semibold [overflow-wrap:anywhere]">
              {name || unnamed}
              <span className="mt-0.5 block text-xs font-medium text-[color:var(--text-tertiary)]">
                {m.trips} {m.trips === 1 ? 'trade' : 'trades'}
                {' · '}
                {m.win_rate_state === 'ok' && m.win_rate !== null ? (
                  <>
                    Win rate <span>{ratioWords(m.win_rate, m.win_rate_state, '%')}</span>
                  </>
                ) : (
                  ratioWords(m.win_rate, m.win_rate_state, '%')
                )}
              </span>
            </th>
            <td className="py-2 text-right font-semibold whitespace-nowrap" data-testid="net">
              <Signed paise={m.net_paise} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function BlockTable({
  title,
  rows,
  first,
  unnamed,
}: {
  title: string
  rows: readonly BlockRow[]
  first: string
  unnamed: string
}) {
  if (rows.length === 0) return null
  return (
    <section aria-label={title} className={`${SECTION} flex flex-col gap-2`}>
      <h2 className="text-base font-bold">{title}</h2>
      <ResultTable label={title} first={first} rows={rows} unnamed={unnamed} />
    </section>
  )
}

/** Rows whose figures came with the answer. */
const present = (rows: Array<{ name: string; metrics: Metrics | undefined }>) =>
  rows.filter((row): row is { name: string; metrics: Metrics } => row.metrics !== undefined)

function ExpirySplit({ split }: { split: AnalyticsAnswer['expiry'] }) {
  const rows = present([
    { name: 'Index expiry day', metrics: split.index_expiry },
    { name: 'Other days', metrics: split.not_index_expiry },
  ])
  const own = present([
    { name: 'Own expiry day', metrics: split.own_expiry },
    { name: 'Not its expiry', metrics: split.not_own_expiry },
  ])
  return (
    <section aria-label="Expiry days" className={`${SECTION} flex flex-col gap-3`}>
      <h2 className="text-base font-bold">Expiry days</h2>
      <p className="text-[0.8125rem] text-[color:var(--text-secondary)]">
        By the day each trade opened.
      </p>
      <ResultTable label="Index expiry" first="Index expiry" rows={rows} unnamed="-" />
      <ResultTable label="Instrument expiry" first="Instrument expiry" rows={own} unnamed="-" />
    </section>
  )
}

/**
 * Whether a block's charges are the journal's estimate: the service says so (`charges_estimated`);
 * an older service that does not say is taken as estimating, as it always did.
 */
export const chargesEstimated = (m: Metrics): boolean => m.charges_estimated !== false

/** The Net tile's note: the count, and "charges est." while any trade's charges are estimated. */
export function netNote(m: Metrics): string {
  const count = `${m.trips} ${m.trips === 1 ? 'trade' : 'trades'}`
  return m.trips > 0 && chargesEstimated(m) ? `${count}, charges est.` : count
}

/** The trades whose segment the charges estimate does not cover (BSE, currency, commodity). */
export function notEstimatedWords(m: Metrics): string | null {
  const n = m.charges_not_estimated ?? 0
  if (n <= 0) return null
  return `${n} ${n === 1 ? 'trade is' : 'trades are'} on an exchange whose charges are not estimated, so no charges are counted for ${n === 1 ? 'it' : 'them'}.`
}

function ChargesLine({ metrics }: { metrics: Metrics }) {
  const missing = notEstimatedWords(metrics)
  return (
    <section aria-label="Charges" className={`${SECTION} flex flex-col gap-1`}>
      <h2 className="text-base font-bold">What charges took</h2>
      <p className="text-[0.9375rem] font-medium" data-testid="charges-line">
        {metrics.charges_impact_pct === null
          ? 'No winning trades in this period, so there is no figure for what charges took of them.'
          : `Charges took ${metrics.charges_impact_pct}% of the gross profit of your winning trades.`}{' '}
        Charges in all: {tileRupees(metrics.charges_paise, false)}
        {chargesEstimated(metrics) && (
          <>
            {' '}
            <span className="text-[color:var(--text-tertiary)]">est.</span>
          </>
        )}
      </p>
      {missing && (
        <p className="text-[0.8125rem] font-medium text-[color:var(--text-secondary)]">{missing}</p>
      )}
    </section>
  )
}

function BetaLine({ beta }: { beta: NonNullable<AnalyticsAnswer['beta']> }) {
  return (
    <section aria-label="Beta" className={`${SECTION} flex flex-col gap-1`}>
      <h2 className="text-base font-bold">Against NIFTY 50</h2>
      <p className="text-[0.9375rem] font-medium">
        {beta.state === 'ok' && beta.beta !== null
          ? `Beta ${beta.beta}${beta.correlation !== null ? `, correlation ${beta.correlation}` : ''} over ${beta.days} trading days.`
          : (beta.note ?? 'Shown after 20 trading days of trades.')}
      </p>
    </section>
  )
}

export function Analytics({ mode }: { mode: Mode }) {
  const [filter, setFilter] = useAnalyticsFilter()
  const [market, setMarket] = useState<MarketCalendar>(CALENDAR_LOADING)
  const day = useIstDay()
  const { anyOpen } = useMarketOpen(market)
  const request = useMemo(() => requestOf(mode, filter), [mode, filter])
  const query = useQuery({
    queryKey: [...JOURNAL_KEY, 'analytics', mode, request],
    queryFn: async () => (request ? journalData('analytics', request) : null),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
    enabled: request !== null,
  })
  const tags = useQuery({
    queryKey: [...JOURNAL_KEY, 'tags'],
    queryFn: async () => journalData('tags_list'),
    retry: false,
    staleTime: 60_000,
  })
  const playbooks = useQuery({
    queryKey: [...JOURNAL_KEY, 'playbooks'],
    queryFn: async () => journalData('playbooks_list'),
    retry: false,
    staleTime: 60_000,
  })
  const { refetch } = query
  const read = useCallback(() => (request ? refetch() : undefined), [refetch, request])
  useJournalSync(read, { marketOpen: anyOpen })

  // The strategies seen in any answer of this mode: a filter on one still lists the others.
  const seen = useRef({ mode, names: new Set<string>() })
  if (seen.current.mode !== mode) seen.current = { mode, names: new Set() }
  for (const row of query.data?.by_strategy ?? []) if (row?.name) seen.current.names.add(row.name)
  if (filter.strategy) seen.current.names.add(filter.strategy)
  const strategies = [...seen.current.names].sort((a, b) => a.localeCompare(b))

  const answer = query.data
  let body: React.ReactNode
  if (request === null) {
    body = (
      <p
        className={`${SECTION} text-center text-sm font-medium text-[color:var(--text-secondary)]`}
      >
        Pick both days to see a custom period.
      </p>
    )
  } else if (query.isPending) {
    body = (
      <div className={`${SECTION} text-center`} aria-live="polite">
        <LoaderCircle aria-hidden="true" className="mx-auto mb-2 size-6 animate-spin" />
        <p className="text-base font-bold">Working out your results</p>
      </div>
    )
  } else if (query.isError && !answer) {
    body = (
      <div className={`${SECTION} text-center`} aria-live="polite">
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{problemOf(query.error).message}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-3 inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else if (answer && !answer.metrics) {
    // Without its figures the answer cannot be read at all.
    body = (
      <div className={`${SECTION} text-center`} aria-live="polite">
        <TriangleAlert aria-hidden="true" className="mx-auto mb-2 size-6" />
        <p className="text-base font-bold">{WORDS.unreadable}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-3 inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <RefreshCw aria-hidden="true" className="size-4" />
          Try again
        </button>
      </div>
    )
  } else if (answer && answer.metrics.trips === 0) {
    body = (
      <p
        className={`${SECTION} text-center text-sm font-medium text-[color:var(--text-secondary)]`}
      >
        {mode === 'sandbox'
          ? 'No closed Sandbox trades in this period. Change the period or the filters.'
          : 'No closed trades in this period. Change the period or the filters.'}
      </p>
    )
  } else if (answer) {
    body = (
      <>
        {query.isError && (
          <Problem>
            {problemOf(query.error).message} The figures below are from the last read.
          </Problem>
        )}
        <Tiles answer={answer} />
        {answer.equity && <EquityChart equity={answer.equity} />}
        {answer.calendar && <CalendarGrid calendar={answer.calendar} />}
        {answer.heat && <HeatMap cells={answer.heat} />}
        {answer.hold && <HoldBars rows={answer.hold} />}
        <BlockTable
          title="By strategy"
          rows={answer.by_strategy ?? []}
          first="Strategy"
          unnamed="No strategy"
        />
        <BlockTable title="By tag" rows={answer.by_tag ?? []} first="Tag" unnamed="No tag" />
        {answer.expiry && <ExpirySplit split={answer.expiry} />}
        <ChargesLine metrics={answer.metrics} />
        {answer.beta && <BetaLine beta={answer.beta} />}
      </>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <MarketCalendarSource key={day} onChange={setMarket} />
      <FilterRow
        filter={filter}
        onChange={setFilter}
        strategies={strategies}
        playbooks={(playbooks.data ?? []).filter((p) => !p.archived || p.id === filter.playbook)}
        tags={(tags.data ?? []).filter((t) => !t.archived || t.id === filter.tag)}
      />
      {body}
    </div>
  )
}
