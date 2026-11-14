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
  CalendarX,
  ChartCandlestick,
  Check,
  EyeOff,
  FlaskConical,
  History,
  LoaderCircle,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { istTime } from '@/sudarshan/books/time'
import { Signed } from '@/sudarshan/portfolio/Signed'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { describe } from '@/sudarshan/watchlist/rows'
import { journalData, problemOf, type SheetFill, type TripDetail, WORDS } from './api'
import { ChargesParts } from './ChargesParts'
import { clearDraft, readDraft, writeDraft } from './drafts'
import { JOURNAL_KEY } from './keys'
import { closingNotSeen, countText, dayWords, holdWords, openQty, priceText } from './live'
import { paiseAmount } from './money'
import { Problem, SideChip, UnsavedQuestion } from './parts'
import {
  buildUpdate,
  changedFields,
  checklistFor,
  formOf,
  MOODS,
  parseRupees,
  problemsOf,
  rebase,
  recentStrategies,
  riskText,
  rText,
  type SheetForm,
} from './sheetForm'
import { NewTag, TagChips } from './TagChips'
import {
  type FillSource,
  LIMITS,
  type Playbook,
  type Tag,
  type Trip,
  type TripUpdateReq,
} from './types'

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
/**
 * The two kinds of position the journal pairs no result for (the service's "unmatched" and
 * "expired"): never a LONG or SHORT, never a live figure, never in the analytics or a report.
 */
export const UNMATCHED_WORDS =
  'This position was opened before the journal started, so the price it was opened at is not known here. Upstox’s own report has it. It is not in your figures.'
/** A trade the journal saw opened and did not see closed (the nightly count against holdings). */
export function unclosedWords(day: number | null): string {
  const when = day === null ? '' : ` on ${dayWords(day)}`
  return `The journal saw this trade opened but did not see it closed. When it checked your Upstox holdings${when}, these shares were not all there; they may have been sold while the journal was off. Upstox’s own report has the result. It is not in your figures.`
}
export const EXPIRED_WORDS =
  'This contract ended at expiry. An expiry has no fill, so the journal cannot work out its result; Upstox’s own report has it. It is not in your figures.'

/** A fill's part in this trade, when it is less than the whole fill (a reversal). */
export function partOf(fill: SheetFill): number | null {
  const part = fill.trip_qty
  return part !== undefined && part > 0 && part < fill.qty ? part : null
}

/**
 * What became of the rest of a fill this trade holds part of: a fill that closed this trade went
 * on into the next one; a fill that opened it closed the one before.
 */
export function restWords(fill: SheetFill, side: Trip['side']): string {
  const entry = (fill.action === 'BUY') === (side === 'long')
  const at = `${istTime(fill.exch_ts_ms)} ${fill.action}`
  return entry
    ? `The rest of the ${at} closed the trade before this one.`
    : `The rest of the ${at} belongs to the next trade.`
}

function FillsTable({
  fills,
  kept,
  side,
}: {
  fills: readonly SheetFill[] | undefined
  kept: boolean
  side: Trip['side']
}) {
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
              <td className="py-2 pr-2 text-right" data-testid="fill-qty">
                {partOf(fill) === null
                  ? countText(fill.qty)
                  : `${countText(partOf(fill) ?? 0)} of ${countText(fill.qty)}`}
              </td>
              <td className="py-2 pr-2 text-right">{priceText(fill.price_u)}</td>
              <td className="py-2">{SOURCE_WORDS[fill.source]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {fills
        .filter((fill) => partOf(fill) !== null)
        .map((fill) => (
          <p
            key={`rest-${fill.trade_id}`}
            className="mt-1 text-xs font-medium text-[color:var(--text-tertiary)]"
          >
            {restWords(fill, side)}
          </p>
        ))}
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

/** A trade on an exchange the charges estimate does not cover: said, never shown as 0.00. */
export const NOT_ESTIMATED = 'Not estimated for this exchange'

/**
 * The word beside the charges: Upstox's actual figure, the journal's estimate, or an estimate
 * from Upstox's trade history (no order ids there: a side's trades of a day are one order).
 */
export function chargesWord(trip: {
  charges_actual_paise: number | null
  charges_actual_basis?: 'day' | 'month' | null
  fills?: SheetFill[]
}) {
  if (trip.charges_actual_paise !== null) {
    // The month's figures answer for a day Upstox never gave by itself.
    return trip.charges_actual_basis === 'month'
      ? 'actual, from Upstox’s figures for the month'
      : trip.charges_actual_basis === 'day'
        ? 'actual, from Upstox’s figures for the day'
        : 'actual'
  }
  return (trip.fills ?? []).some((f) => f.source === 'history')
    ? 'est., from Upstox’s history'
    : 'est.'
}

/** What kind of record the sheet shows, under its title. */
export function sheetDescription(status: Trip['status'], unclosed = false): string {
  if (status === 'open') return 'Open trade'
  if (status === 'orphan') return 'Earlier record, kept for your notes'
  if (status === 'unmatched' && unclosed) return 'Closing not seen by the journal'
  if (status === 'unmatched') return 'Opened before the journal started'
  if (status === 'expired') return 'Ended at expiry'
  return 'Closed trade'
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

/** The trade's changes as the sheet holds them (reviews H2, H3, M1). */
interface Draft {
  id: number
  /** The trade's version the form now stands on: Save sends it. */
  version: number
  /** The form as the service holds it at that version, and as the owner has changed it. */
  base: SheetForm
  form: SheetForm
  /** Notes typed here that the other screen's notes replaced in the field: offered back. */
  typedNotes: string | null
  /** The other screen also changed these; its values are shown. */
  overlap: string[]
  /** Changes this tab kept from before came back into the form. */
  restored: boolean
  /** A change made on another screen came in while the form held changes of its own. */
  moved: boolean
}

function freshDraft(trip: TripDetail, base: SheetForm): Draft {
  return {
    id: trip.id,
    version: trip.version,
    base,
    form: base,
    typedNotes: null,
    overlap: [],
    restored: false,
    moved: false,
  }
}

/**
 * The draft after a read of the trade. The same version keeps everything. A newer version (a Save
 * on another screen, "Open it again" after a conflict) takes the new values where the owner changed
 * nothing and keeps what he typed where he did (`rebase`), so a re-read never drops his words. The
 * first read of a trade brings back what this tab kept of it.
 */
function nextDraft(prev: Draft | null, trip: TripDetail): Draft {
  const theirs = formOf(trip, trip.note)
  if (prev && prev.id === trip.id) {
    if (prev.version === trip.version) return prev
    if (changedFields(prev.base, prev.form).length === 0 && prev.typedNotes === null) {
      return freshDraft(trip, theirs)
    }
    const merged = rebase(prev.base, prev.form, theirs)
    const typedNotes = merged.typedNotes ?? prev.typedNotes
    if (changedFields(theirs, merged.form).length === 0 && typedNotes === null) {
      // The newer read holds just what was typed (this sheet's own Save coming back): nothing to
      // say about another screen.
      return freshDraft(trip, theirs)
    }
    return {
      ...freshDraft(trip, theirs),
      form: merged.form,
      typedNotes,
      overlap: merged.overlap,
      restored: prev.restored,
      moved: true,
    }
  }
  const kept = readDraft(trip.id)
  if (!kept) return freshDraft(trip, theirs)
  if (kept.version === trip.version) {
    return {
      ...freshDraft(trip, theirs),
      form: kept.form,
      typedNotes: kept.typedNotes,
      restored: true,
    }
  }
  const merged = rebase(kept.base, kept.form, theirs)
  return {
    ...freshDraft(trip, theirs),
    form: merged.form,
    typedNotes: merged.typedNotes ?? kept.typedNotes,
    overlap: merged.overlap,
    restored: true,
    moved: true,
  }
}

const QUIET_BUTTON =
  'inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent'

/** Notes typed here that another screen's notes replaced: shown whole, to add back or copy. */
function TypedNotes({ text, onAdd }: { text: string; onAdd: () => void }) {
  const [copied, setCopied] = useState(false)
  const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
  return (
    <div
      data-testid="typed-notes"
      className="flex flex-col gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] px-3 py-2"
    >
      <p className="text-sm font-semibold">
        The notes were changed on another screen; they are in the field above. What you typed here
        is not saved:
      </p>
      <p className="text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">{text}</p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <button type="button" onClick={onAdd} className={QUIET_BUTTON}>
          Add it to the notes
        </button>
        {clipboard?.writeText && (
          <button
            type="button"
            onClick={() =>
              void clipboard.writeText(text).then(
                () => setCopied(true),
                () => setCopied(false)
              )
            }
            className={QUIET_BUTTON}
          >
            {copied ? <Check aria-hidden="true" className="size-4" strokeWidth={3} /> : null}
            {copied ? 'Copied' : 'Copy it'}
          </button>
        )}
      </div>
    </div>
  )
}

function Editor({
  trip,
  draft,
  tags,
  playbooks,
  recent,
  saving,
  update,
  problem,
  onChange,
  onSave,
  onReopen,
}: {
  trip: Trip
  draft: Draft
  tags: readonly Tag[]
  playbooks: readonly Playbook[]
  recent: readonly string[]
  saving: boolean
  update: TripUpdateReq | null
  problem: { message: string; conflict: boolean } | null
  onChange: (change: (now: Draft) => Draft) => void
  onSave: () => void
  onReopen: () => void
}) {
  const queries = useQueryClient()
  const [tagBusy, setTagBusy] = useState(false)
  const [tagProblem, setTagProblem] = useState<string | null>(null)
  const form = draft.form
  const setForm = (change: (now: SheetForm) => SheetForm) =>
    onChange((now) => ({ ...now, form: change(now.form) }))
  const set = <K extends keyof SheetForm>(key: K, value: SheetForm[K]) =>
    setForm((now) => ({ ...now, [key]: value }))

  const problems = problemsOf(form)
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
      if (done.id !== null) {
        const id = done.id
        setForm((now) => ({ ...now, mistakes: [...now.mistakes, id] }))
      }
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
        onSave()
      }}
    >
      {draft.restored && (
        <output className="block text-sm font-medium text-[color:var(--text-secondary)]">
          What you typed on this trade before is back in the form. It is not saved yet.
        </output>
      )}
      {draft.moved && (
        <output className="block text-sm font-medium text-[color:var(--text-secondary)]">
          This trade was changed on another screen. Its changes are shown, and what you typed here
          is kept.
          {draft.overlap.length > 0 &&
            ` It also changed ${draft.overlap.join(', ')}; what it saved is shown there.`}
        </output>
      )}
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
        {draft.typedNotes !== null && (
          <TypedNotes
            text={draft.typedNotes}
            onAdd={() =>
              onChange((now) => ({
                ...now,
                typedNotes: null,
                form: {
                  ...now.form,
                  notes: now.form.notes
                    ? `${now.form.notes}\n\n${now.typedNotes ?? ''}`
                    : (now.typedNotes ?? ''),
                },
              }))
            }
          />
        )}
      </Section>

      {problems.map((text) => (
        <Problem key={text}>{text}</Problem>
      ))}
      {problem && (
        <div className="flex flex-col gap-2">
          <Problem>{problem.message}</Problem>
          {problem.conflict && (
            <button type="button" onClick={onReopen} className={QUIET_BUTTON}>
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
  onDirty,
  asked = 0,
}: {
  tradeId: number
  open: boolean
  onClose: () => void
  /** Told whenever the sheet starts or stops holding changes that are not saved. */
  onDirty?: (dirty: boolean) => void
  /** Moved on by the page when Back took the sheet's entry while it held changes: ask first. */
  asked?: number
}) {
  const queries = useQueryClient()
  const [draft, setDraft] = useState<Draft | null>(null)
  const dirtyNow =
    draft !== null &&
    (changedFields(draft.base, draft.form).length > 0 || draft.typedNotes !== null)
  const detail = useQuery({
    queryKey: [...JOURNAL_KEY, 'trip', tradeId],
    queryFn: async () => journalData('trip_get', { id: tradeId }),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
    // A phone changes network often; while the form holds changes a new network does not read the
    // trade again under them (review H3). The version check at Save guards a stale sheet.
    refetchOnReconnect: !dirtyNow,
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
  // The contract as the books name it (review H5), never the code cut short in the title.
  const named = trip ? describe(trip.symbol, trip.exchange) : null

  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<{ message: string; conflict: boolean } | null>(null)
  const [asking, setAsking] = useState(false)
  const sending = useRef(false)

  // Each read of the trade moves the draft on (nextDraft); a newer version clears a conflict.
  const [read, setRead] = useState<TripDetail | undefined>(undefined)
  if (trip !== read) {
    setRead(trip)
    if (trip) {
      const next = nextDraft(draft, trip)
      if (next !== draft) {
        if (draft && next.version !== draft.version) setProblem(null)
        setDraft(next)
      }
    }
  }
  // Back asked to close while the sheet held changes: the question, as for Close.
  const [seenAsked, setSeenAsked] = useState(asked)
  if (asked !== seenAsked) {
    setSeenAsked(asked)
    setAsking(true)
  }

  const update = useMemo(
    () => (trip && draft ? buildUpdate(trip, draft.base, draft.form) : null),
    [trip, draft]
  )
  const dirty = update !== null || (draft !== null && draft.typedNotes !== null)
  const problems = draft ? problemsOf(draft.form) : []

  // What is not saved is kept in this tab while it is there, and the page is told.
  const discarded = useRef(false)
  useEffect(() => {
    if (!draft || discarded.current) return
    if (dirty) {
      writeDraft(draft.id, {
        version: draft.version,
        base: draft.base,
        form: draft.form,
        typedNotes: draft.typedNotes,
      })
    } else clearDraft(draft.id)
  }, [draft, dirty])
  const told = useRef(onDirty)
  told.current = onDirty
  useEffect(() => {
    told.current?.(dirty)
  }, [dirty])

  const finish = () => {
    if (draft) clearDraft(draft.id)
    discarded.current = true
    told.current?.(false)
    onClose()
  }

  const save = async () => {
    // One Save, one request, however it is tapped.
    if (sending.current || update === null) return
    if (problems.length > 0) {
      setAsking(false) // the sentences that say what to correct are under the form
      return
    }
    sending.current = true
    setSaving(true)
    setProblem(null)
    try {
      await journalData('trip_update', update)
      if (draft) clearDraft(draft.id)
      await queries.invalidateQueries({ queryKey: JOURNAL_KEY })
      finish()
    } catch (error) {
      const found = problemOf(error)
      const conflict = found.code === 'conflict'
      setProblem({ message: conflict ? WORDS.conflict : found.message, conflict })
      setAsking(false)
    } finally {
      sending.current = false
      setSaving(false)
    }
  }

  // Close, a tap outside, Escape: with changes not saved, ask first (review H2).
  const requestClose = () => {
    if (asking) setAsking(false)
    else if (dirty) setAsking(true)
    else finish()
  }

  let body: React.ReactNode
  if (!trip || !draft) {
    body = detail.isPending ? (
      <p className="flex items-center gap-2 py-6 text-base font-bold" aria-live="polite">
        <LoaderCircle aria-hidden="true" className="size-5 animate-spin" />
        Loading the trade
      </p>
    ) : (
      <div className="flex flex-col gap-3 py-2">
        <Problem>{problemOf(detail.error).message}</Problem>
        <button type="button" onClick={() => detail.refetch()} className={QUIET_BUTTON}>
          Try again
        </button>
      </div>
    )
  } else {
    const kept = trip.status === 'orphan'
    const unclosed = closingNotSeen(trip)
    const unseen = trip.status === 'unmatched' && !unclosed
    const expired = trip.status === 'expired'
    const aside = unseen || unclosed || expired
    const closed = trip.status !== 'open' && !expired
    const stillOpen = unclosed ? 'Not seen' : kept || unseen ? '-' : 'Still open'
    const notKnown = unseen || unclosed ? 'Not known' : 'Not worked out'
    const charges = trip.charges_actual_paise ?? trip.charges_est_paise
    const unpriced = trip.charges_modelled === false && trip.charges_actual_paise === null
    const estimate = chargesWord(trip)
    const capture = captureOf(trip.net_paise, trip.mfe_paise, trip.excursion?.capture_pct)
    body = (
      <>
        {asking && (
          <UnsavedQuestion
            title="Your changes to this trade are not saved."
            canSave={update !== null}
            saving={saving}
            onSave={() => void save()}
            onDiscard={finish}
            onKeep={() => setAsking(false)}
          />
        )}
        <div className="flex flex-col gap-4" hidden={asking}>
          {detail.isError && (
            // A re-read that failed keeps the sheet and what was typed in it (review H3).
            <Problem>
              {problemOf(detail.error).message} What you see is from the last read, and what you
              typed is still here.
            </Problem>
          )}
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
          {aside && (
            <p
              role="note"
              data-testid="aside-note"
              className="flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--border-strong)] px-3 py-2 text-sm font-medium"
            >
              {unclosed ? (
                <EyeOff aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              ) : unseen ? (
                <History aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              ) : (
                <CalendarX aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              )}
              {unclosed ? unclosedWords(trip.close_day) : unseen ? UNMATCHED_WORDS : EXPIRED_WORDS}
            </p>
          )}
          <Section title="Result">
            <dl className="flex flex-col gap-1.5">
              <Row label="Quantity">{countText(closed ? trip.qty_max : openQty(trip))}</Row>
              <Row label="Entry average">{unseen ? notKnown : priceText(trip.entry_avg_u)}</Row>
              <Row label="Exit average">
                {expired
                  ? 'Ended at expiry'
                  : trip.exit_avg_u === null
                    ? stillOpen
                    : priceText(trip.exit_avg_u)}
              </Row>
              <Row label="Gross">
                {aside ? (
                  notKnown
                ) : trip.gross_paise === null ? (
                  '-'
                ) : (
                  <Signed paise={trip.gross_paise} />
                )}
              </Row>
              <Row label="Charges">
                {unpriced ? (
                  NOT_ESTIMATED
                ) : charges === null ? (
                  '-'
                ) : (
                  <>
                    {paiseAmount(charges)}{' '}
                    <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                      {estimate}
                    </span>
                  </>
                )}
              </Row>
              {trip.charges_actual && (
                <ChargesParts actual={trip.charges_actual} estimate={trip.charges_est_paise} />
              )}
              {trip.charges_note && (
                <p
                  data-testid="trip-charges-note"
                  className="text-xs text-[color:var(--text-secondary)]"
                >
                  {trip.charges_note}
                </p>
              )}
              <Row label="Net">
                {aside ? (
                  notKnown
                ) : trip.net_paise === null ? (
                  '-'
                ) : (
                  <span className="inline-flex items-baseline gap-1.5">
                    <Signed paise={trip.net_paise} />
                    {trip.charges_actual_paise === null && (
                      <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
                        {unpriced ? 'no charges counted' : 'est.'}
                      </span>
                    )}
                  </span>
                )}
              </Row>
              <Row label="Held">
                {aside ? '-' : trip.hold_s === null ? stillOpen : holdWords(trip.hold_s * 1000)}
              </Row>
              {trip.latency_ms !== null && trip.latency_ms !== undefined && (
                <Row label="First entry order">answered in {countText(trip.latency_ms)} ms</Row>
              )}
            </dl>
          </Section>
          <Section title="Fills">
            <FillsTable fills={trip.fills} kept={kept} side={trip.side} />
          </Section>
          {!aside && (
            <Section title="How far it went">
              <Excursion trip={trip} capture={capture} />
            </Section>
          )}
          <Editor
            trip={trip}
            draft={draft}
            tags={tags.data ?? []}
            playbooks={playbooks.data ?? []}
            recent={recent}
            saving={saving}
            update={update}
            problem={problem}
            onChange={(change) => setDraft((now) => (now ? change(now) : now))}
            onSave={() => void save()}
            onReopen={() => void detail.refetch()}
          />
          <Link
            to={`/chart/${encodeURIComponent(trip.exchange)}/${encodeURIComponent(trip.symbol)}`}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-[0.875rem] border border-input bg-card px-4 text-[0.9375rem] font-semibold hover:bg-accent"
          >
            <ChartCandlestick aria-hidden="true" className="size-4" />
            See on chart
          </Link>
        </div>
      </>
    )
  }

  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) requestClose()
      }}
      step={asking ? 'ask' : 'form'}
      title={
        <span className="inline-flex items-center gap-2">
          {named ? named.title : 'Trade'}
          {trip && (trip.status !== 'unmatched' || closingNotSeen(trip)) && (
            <SideChip side={trip.side} />
          )}
        </span>
      }
      description={
        trip && named ? (
          <>
            {sheetDescription(trip.status, closingNotSeen(trip))}
            <span>{` · ${named.detail}`}</span>
          </>
        ) : undefined
      }
    >
      {body}
    </BottomSheet>
  )
}
