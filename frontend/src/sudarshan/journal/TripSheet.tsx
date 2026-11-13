// SUDARSHAN M14.4, M14.5, JD3: one trade in a sheet. What happened (fills, averages, gross,
// charges, net, hold, how far it went for and against, how fast its first entry order was
// answered) and what the owner makes of it (strategy, playbook, risk, checklist, mistakes, mood,
// notes). Save sends once, with the version the sheet was opened at; a conflict is said in one
// sentence and nothing is saved. Prices and money are the contract's integers, shown through
// dashboard/format.ts.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDown,
  ArrowUp,
  ChartCandlestick,
  Check,
  FlaskConical,
  History,
  LoaderCircle,
} from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { istTime } from '@/sudarshan/books/time'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { journalData, problemOf, WORDS } from './api'
import { JOURNAL_KEY } from './keys'
import { holdWords, openQty, priceText } from './live'
import { paiseAmount } from './money'
import { Problem, SideChip } from './parts'
import {
  buildUpdate,
  checklistFor,
  formOf,
  MOODS,
  parseRupees,
  problemsOf,
  recentStrategies,
  riskText,
  rText,
  type SheetForm,
} from './sheetForm'
import { NewTag, TagChips } from './TagChips'
import { type Fill, type FillSource, LIMITS, type Playbook, type Tag, type Trip } from './types'

const SOURCE_WORDS: Record<FillSource, string> = {
  stream_read: 'Order stream',
  close_reconcile: 'Close check',
  history: 'History',
  sandbox_book: 'Sandbox',
  withdrawn: 'Withdrawn',
}

const LABEL = 'text-[0.8125rem] font-semibold text-[color:var(--text-secondary)]'
const FIELD =
  'min-h-12 w-full rounded-[0.875rem] border border-input bg-card px-3 text-[0.9375rem] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      className="flex flex-col gap-2 border-t border-[color:var(--divider)] pt-3"
    >
      <h3 className="text-[0.9375rem] font-bold">{title}</h3>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[0.9375rem]">
      <dt className="font-medium text-[color:var(--text-secondary)]">{label}</dt>
      <dd className="text-right font-semibold">{children}</dd>
    </div>
  )
}

/**
 * A trade the journal paired again after the owner wrote on it is kept as it was, with no fills
 * of its own, so his words are not lost (the service's "orphan"; it is never in the lists).
 */
export const ORPHAN_WORDS =
  'This trade’s fills changed after you wrote on it, so the journal paired them again. This is the record as it was, kept so your notes are not lost; the trade as it is now is in the list.'
export const ORPHAN_FILLS =
  'This earlier record keeps no fills: they belong to the trade as it is now.'

function FillsTable({ fills, kept }: { fills: readonly Fill[] | undefined; kept: boolean }) {
  if (!fills || fills.length === 0) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        {kept
          ? ORPHAN_FILLS
          : 'The fills of this trade are not available yet. Open it again in a minute.'}
      </p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[20rem] text-left text-[0.8125rem]">
        <caption className="sr-only">Fills</caption>
        <thead className="text-[color:var(--text-secondary)]">
          <tr>
            <th scope="col" className="py-1 pr-2 font-semibold">
              Time
            </th>
            <th scope="col" className="py-1 pr-2 font-semibold">
              Side
            </th>
            <th scope="col" className="py-1 pr-2 text-right font-semibold">
              Qty
            </th>
            <th scope="col" className="py-1 pr-2 text-right font-semibold">
              Price
            </th>
            <th scope="col" className="py-1 font-semibold">
              Source
            </th>
          </tr>
        </thead>
        <tbody>
          {fills.map((fill) => (
            <tr key={fill.trade_id} className="border-t border-[color:var(--divider)]">
              <td className="py-2 pr-2">{istTime(fill.exch_ts_ms)}</td>
              <td className="py-2 pr-2">
                <span className="inline-flex items-center gap-0.5 font-semibold">
                  {fill.action === 'BUY' ? (
                    <ArrowUp aria-hidden="true" className="size-3" strokeWidth={3} />
                  ) : (
                    <ArrowDown aria-hidden="true" className="size-3" strokeWidth={3} />
                  )}
                  {fill.action}
                </span>
              </td>
              <td className="py-2 pr-2 text-right">{fill.qty}</td>
              <td className="py-2 pr-2 text-right">{priceText(fill.price_u)}</td>
              <td className="py-2">{SOURCE_WORDS[fill.source]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Excursion({ trip, capture }: { trip: Trip; capture: string | null }) {
  if (trip.mae_paise === null || trip.mfe_paise === null) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        {trip.excursion_note ?? 'How far this trade went for and against you was not worked out.'}
      </p>
    )
  }
  return (
    <dl className="flex flex-col gap-1.5">
      <Row label="Worst point (MAE)">
        <Signed paise={trip.mae_paise} />
        {trip.mae_ms !== null && (
          <span className="ml-2 text-xs font-medium text-[color:var(--text-tertiary)]">
            at {istTime(trip.mae_ms)}
          </span>
        )}
      </Row>
      <Row label="Best point (MFE)">
        <Signed paise={trip.mfe_paise} />
        {trip.mfe_ms !== null && (
          <span className="ml-2 text-xs font-medium text-[color:var(--text-tertiary)]">
            at {istTime(trip.mfe_ms)}
          </span>
        )}
      </Row>
      {capture !== null && <Row label="Kept of the best">{capture}%</Row>}
      {trip.excursion_note && (
        <p className="text-xs text-[color:var(--text-tertiary)]">{trip.excursion_note}</p>
      )}
    </dl>
  )
}

/** net / best point as a percentage, when there is a best point above zero. */
function captureOf(net: number | null, mfe: number | null, given: string | null | undefined) {
  if (given) return given
  if (net === null || mfe === null || mfe <= 0) return null
  const hundredths = Math.round((net * 10_000) / mfe)
  const sign = hundredths < 0 ? '−' : ''
  const magnitude = Math.abs(hundredths)
  return `${sign}${Math.floor(magnitude / 100)}.${String(magnitude % 100).padStart(2, '0')}`
}

function Editor({
  trip,
  start,
  tags,
  playbooks,
  recent,
  onSaved,
  onReopen,
}: {
  trip: Trip
  start: SheetForm
  tags: readonly Tag[]
  playbooks: readonly Playbook[]
  recent: readonly string[]
  onSaved: () => void
  onReopen: () => void
}) {
  const queries = useQueryClient()
  const [form, setForm] = useState<SheetForm>(start)
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<{ message: string; conflict: boolean } | null>(null)
  const [tagBusy, setTagBusy] = useState(false)
  const [tagProblem, setTagProblem] = useState<string | null>(null)
  const sending = useRef(false)
  const set = <K extends keyof SheetForm>(key: K, value: SheetForm[K]) =>
    setForm((now) => ({ ...now, [key]: value }))

  const problems = problemsOf(form)
  const update = useMemo(() => buildUpdate(trip, start, form), [trip, start, form])
  const playbook = playbooks.find((p) => p.id === form.setupId) ?? null
  const risk = parseRupees(form.risk)
  const r = rText(trip.net_paise, risk.paise)

  const pickPlaybook = (id: number | null) => {
    const next = playbooks.find((p) => p.id === id) ?? null
    setForm((now) => ({
      ...now,
      setupId: id,
      checklist: next ? checklistFor(next.rules, now.checklist) : [],
      risk:
        now.risk === '' && next?.default_risk_paise ? riskText(next.default_risk_paise) : now.risk,
    }))
  }

  const save = async () => {
    // One Save, one request, however it is tapped.
    if (sending.current || update === null || problems.length > 0) return
    sending.current = true
    setSaving(true)
    setProblem(null)
    try {
      await journalData('trip_update', update)
      await queries.invalidateQueries({ queryKey: JOURNAL_KEY })
      onSaved()
    } catch (error) {
      const found = problemOf(error)
      const conflict = found.code === 'conflict'
      setProblem({ message: conflict ? WORDS.conflict : found.message, conflict })
    } finally {
      sending.current = false
      setSaving(false)
    }
  }

  const addTag = async (name: string): Promise<boolean> => {
    setTagBusy(true)
    setTagProblem(null)
    try {
      const done = await journalData('tag_create', {
        kind: 'mistake',
        name,
        colour: 'neutral',
        request_id: crypto.randomUUID(),
      })
      await queries.invalidateQueries({ queryKey: [...JOURNAL_KEY, 'tags'] })
      if (done.id !== null) set('mistakes', [...form.mistakes, done.id])
      return true
    } catch (error) {
      setTagProblem(problemOf(error).message)
      return false
    } finally {
      setTagBusy(false)
    }
  }

  const strategy = form.strategy.trim()
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <Section title="Strategy and setup">
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Strategy (up to {LIMITS.strategy_name} characters)</span>
          <input
            type="text"
            value={form.strategy}
            maxLength={LIMITS.strategy_name + 20}
            placeholder={trip.strategy || 'No strategy'}
            onChange={(event) => set('strategy', event.target.value)}
            className={FIELD}
          />
        </label>
        {recent.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Strategies used lately">
            {recent.map((name) => (
              <li key={name}>
                <button
                  type="button"
                  aria-pressed={strategy === name}
                  onClick={() => set('strategy', name)}
                  className={cn(
                    'inline-flex min-h-12 items-center gap-1.5 rounded-full border px-3.5 text-[0.875rem] font-semibold',
                    strategy === name
                      ? 'border-[color:var(--brand)] bg-[color:var(--brand-tint)] text-[color:var(--brand)]'
                      : 'border-[color:var(--border-strong)] text-[color:var(--text-secondary)]'
                  )}
                >
                  {name}
                  {strategy === name && (
                    <Check aria-hidden="true" className="size-4" strokeWidth={3} />
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Playbook</span>
          <select
            value={form.setupId ?? ''}
            onChange={(event) =>
              pickPlaybook(event.target.value === '' ? null : Number(event.target.value))
            }
            className={FIELD}
          >
            <option value="">No playbook</option>
            {playbooks
              .filter((p) => !p.archived || p.id === form.setupId)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
      </Section>

      <Section title="Risk">
        <label className="flex flex-col gap-1">
          <span className={LABEL}>Risk in rupees (optional)</span>
          <input
            type="text"
            inputMode="decimal"
            value={form.risk}
            placeholder="For example 500"
            onChange={(event) => set('risk', event.target.value)}
            aria-invalid={!risk.valid}
            className={FIELD}
          />
        </label>
        {r !== null && (
          <p className="text-[0.9375rem] font-semibold" data-testid="r-multiple">
            R {r}
          </p>
        )}
      </Section>

      {playbook && playbook.rules.length > 0 && (
        <Section title="Checklist">
          <ul className="flex flex-col gap-1">
            {form.checklist.map((item, n) => (
              <li key={item.rule}>
                <fieldset
                  aria-label={item.rule}
                  className="m-0 flex min-h-12 min-w-0 items-center justify-between gap-3 border-0 p-0"
                >
                  <span className="text-[0.9375rem] font-medium">{item.rule}</span>
                  <span className="inline-flex shrink-0 overflow-hidden rounded-[0.75rem] border border-[color:var(--border-strong)]">
                    {([true, false] as const).map((yes) => (
                      <button
                        key={String(yes)}
                        type="button"
                        aria-pressed={item.yes === yes}
                        onClick={() =>
                          set(
                            'checklist',
                            form.checklist.map((x, i) => (i === n ? { ...x, yes } : x))
                          )
                        }
                        className={cn(
                          'min-h-12 min-w-12 px-3 text-[0.875rem] font-semibold',
                          item.yes === yes
                            ? 'bg-[color:var(--brand)] text-white'
                            : 'text-[color:var(--text-secondary)]'
                        )}
                      >
                        {yes ? 'Yes' : 'No'}
                      </button>
                    ))}
                  </span>
                </fieldset>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Mistakes">
        <TagChips
          tags={tags.filter((t) => t.kind === 'mistake')}
          selected={form.mistakes}
          onToggle={(id) =>
            set(
              'mistakes',
              form.mistakes.includes(id)
                ? form.mistakes.filter((m) => m !== id)
                : [...form.mistakes, id]
            )
          }
          disabled={saving}
        />
        <NewTag onAdd={addTag} busy={tagBusy} problem={tagProblem} />
      </Section>

      <Section title="Mood">
        <ul className="flex flex-wrap gap-2" aria-label="Mood">
          {MOODS.map(({ id, label }) => {
            const on = form.mood === id
            return (
              <li key={id}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => set('mood', on ? null : id)}
                  className={cn(
                    'inline-flex min-h-12 items-center gap-1.5 rounded-full border px-3.5 text-[0.875rem] font-semibold',
                    on
                      ? 'border-[color:var(--brand)] bg-[color:var(--brand-tint)] text-[color:var(--brand)]'
                      : 'border-[color:var(--border-strong)] text-[color:var(--text-secondary)]'
                  )}
                >
                  {label}
                  {on && <Check aria-hidden="true" className="size-4" strokeWidth={3} />}
                </button>
              </li>
            )
          })}
        </ul>
      </Section>

      <Section title="Notes">
        <div className="flex flex-col gap-1">
          <textarea
            aria-label="Notes"
            value={form.notes}
            rows={4}
            onChange={(event) => set('notes', event.target.value)}
            className={cn(FIELD, 'py-2')}
          />
          <span className="self-end text-xs font-medium text-[color:var(--text-tertiary)]">
            {form.notes.length.toLocaleString('en-IN')} of {LIMITS.notes.toLocaleString('en-IN')}
          </span>
        </div>
      </Section>

      {problems.map((text) => (
        <Problem key={text}>{text}</Problem>
      ))}
      {problem && (
        <div className="flex flex-col gap-2">
          <Problem>{problem.message}</Problem>
          {problem.conflict && (
            <button
              type="button"
              onClick={onReopen}
              className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
            >
              Open it again
            </button>
          )}
        </div>
      )}

      <button
        type="submit"
        disabled={saving || update === null || problems.length > 0}
        className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] bg-[color:var(--brand)] px-4 text-[0.9375rem] font-bold text-white outline-none hover:bg-[color:var(--brand-hover)] focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60"
      >
        {saving && <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />}
        {saving ? 'Saving' : update === null ? 'No changes to save' : 'Save'}
      </button>
    </form>
  )
}

/** The trade's sheet: what happened, then what the owner makes of it. */
export function TripSheet({
  tradeId,
  open,
  onClose,
}: {
  tradeId: number
  open: boolean
  onClose: () => void
}) {
  const queries = useQueryClient()
  const detail = useQuery({
    queryKey: [...JOURNAL_KEY, 'trip', tradeId],
    queryFn: async () => journalData('trip_get', { id: tradeId }),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
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
  const trip = detail.data
  // The sheet opens on a list that is already read; it is not read again for the strategies.
  const [recent] = useState(() => {
    const seen: Trip[] = []
    for (const [, rows] of queries.getQueriesData<{ open?: Trip[]; closed?: Trip[] }>({
      queryKey: [...JOURNAL_KEY, 'trips'],
    })) {
      seen.push(...(rows?.open ?? []), ...(rows?.closed ?? []))
    }
    return recentStrategies(seen)
  })
  const start = useMemo(() => (trip ? formOf(trip, trip.note) : null), [trip])

  let body: React.ReactNode
  if (detail.isPending) {
    body = (
      <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
        Loading the trade
      </p>
    )
  } else if (detail.isError || !trip || !start) {
    body = (
      <div className="flex flex-col gap-3 py-2">
        <Problem>{problemOf(detail.error).message}</Problem>
        <button
          type="button"
          onClick={() => detail.refetch()}
          className="inline-flex min-h-12 items-center justify-center rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          Try again
        </button>
      </div>
    )
  } else {
    const closed = trip.status !== 'open'
    const kept = trip.status === 'orphan'
    const stillOpen = kept ? '-' : 'Still open'
    const charges = trip.charges_actual_paise ?? trip.charges_est_paise
    const capture = captureOf(trip.net_paise, trip.mfe_paise, trip.excursion?.capture_pct)
    body = (
      <div className="flex flex-col gap-4">
        {trip.mode === 'sandbox' && (
          <p className="flex items-center gap-2 rounded-[0.875rem] border border-[color:var(--sandbox-border)] bg-[color:var(--sandbox-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--sandbox-ink)]">
            <FlaskConical aria-hidden="true" className="size-4 shrink-0" />
            Sandbox trade
          </p>
        )}
        {kept && (
          <p
            role="note"
            className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] px-3 py-2 text-sm font-medium"
          >
            <History aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {ORPHAN_WORDS}
          </p>
        )}
        <Section title="Result">
          <dl className="flex flex-col gap-1.5">
            <Row label="Quantity">{closed ? trip.qty_max : openQty(trip)}</Row>
            <Row label="Entry average">{priceText(trip.entry_avg_u)}</Row>
            <Row label="Exit average">
              {trip.exit_avg_u === null ? stillOpen : priceText(trip.exit_avg_u)}
            </Row>
            <Row label="Gross">
              {trip.gross_paise === null ? '-' : <Signed paise={trip.gross_paise} />}
            </Row>
            <Row label="Charges">
              {charges === null ? (
                '-'
              ) : (
                <>
                  {paiseAmount(charges)}{' '}
                  <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                    {trip.charges_actual_paise === null ? 'est.' : 'actual'}
                  </span>
                </>
              )}
            </Row>
            <Row label="Net">
              {trip.net_paise === null ? (
                '-'
              ) : (
                <span className="inline-flex items-baseline gap-1.5">
                  <Signed paise={trip.net_paise} />
                  {trip.charges_actual_paise === null && (
                    <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                      est.
                    </span>
                  )}
                </span>
              )}
            </Row>
            <Row label="Held">
              {trip.hold_s === null ? stillOpen : holdWords(trip.hold_s * 1000)}
            </Row>
            {trip.latency_ms !== null && trip.latency_ms !== undefined && (
              <Row label="First entry order">answered in {trip.latency_ms} ms</Row>
            )}
          </dl>
        </Section>
        <Section title="Fills">
          <FillsTable fills={trip.fills} kept={kept} />
        </Section>
        <Section title="How far it went">
          <Excursion trip={trip} capture={capture} />
        </Section>
        <Editor
          key={`${trip.id}:${trip.version}`}
          trip={trip}
          start={start}
          tags={tags.data ?? []}
          playbooks={playbooks.data ?? []}
          recent={recent}
          onSaved={onClose}
          onReopen={() => detail.refetch()}
        />
        <Link
          to={`/chart/${encodeURIComponent(trip.exchange)}/${encodeURIComponent(trip.symbol)}`}
          className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
        >
          <ChartCandlestick aria-hidden="true" className="size-4" />
          See on chart
        </Link>
      </div>
    )
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={
        <span className="inline-flex items-center gap-2">
          {trip ? trip.symbol : 'Trade'}
          {trip && <SideChip side={trip.side} />}
        </span>
      }
      description={
        trip
          ? trip.status === 'open'
            ? 'Open trade'
            : trip.status === 'orphan'
              ? 'Earlier record, kept for your notes'
              : 'Closed trade'
          : undefined
      }
    >
      {body}
    </BottomSheet>
  )
}
