// SUDARSHAN P7 F1 (M7.27): the Tax view, `/portfolio?view=tax` (design p7 9.1): one taxpayer's
// financial year as the tax core worked it out (`tax_get`). A working paper to verify with the CA,
// and the notice that says so stays on screen however far the page is scrolled. Tax is not market
// data: the meta line says when it was worked out and how old the read of Upstox is, and nothing
// here says "Real-time". The owner's own page only; a family viewer is never offered this view.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Clock, Info, PencilLine, RefreshCw } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { cn } from '@/lib/utils'
import { useNow } from '@/sudarshan/dashboard/hooks'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { dayOfInstant } from '../days'
import { DividendsCard } from '../dividends/DividendsCard'
import { type Member, REPORT_YEARS, type TaxPack } from '../types'
import { asOf, fyLabel, fyOfDay } from './format'
import { InputsSheet } from './InputsSheet'
import { TAX_KEY } from './keys'
import { ChargesCard, Downloads, LeftOutCard, LossesCard, ReadsCard, readingOf } from './MoreCards'
import { CARD, Card, OUTLINE, Sentences } from './parts'
import { ScheduleSheet } from './ScheduleSheet'
import { BusinessCard, CapitalGainsCard } from './TaxCards'
import { type TaxSheet, useTaxSheet } from './useTaxSheet'

export { TAX_KEY }
/** The view's own sentence while its switch is off on the server (the op answers `not_ready`). */
export const TAX_OFF =
  'The tax working paper is switched off on the server. It opens here once a release switches it on.'
export const TAX_NOTICE =
  'Working paper. Verify with your CA. Upstox’s own report is the filing source.'

/** How often the paper is asked for again while a part of the year is being read. */
const READING_MS = 5000

export function taxOptions(fy: number, member: number | undefined) {
  return {
    queryKey: [...TAX_KEY, 'pack', fy, member ?? null] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<TaxPack> => {
      const result = await portfolioCall('tax_get', { fy, member }, signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    retry: false,
    refetchOnWindowFocus: false,
  }
}

const PICK =
  'inline-flex min-h-12 flex-1 items-center justify-center rounded-[0.875rem] px-2 text-[0.9375rem] whitespace-nowrap outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

/** One of a few choices as a row of buttons; the chosen one is told by tint, edge and weight. */
export function Picker<V extends number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: readonly { value: V; label: string }[]
  value: V
  onChange: (value: V) => void
}) {
  return (
    <fieldset aria-label={label} className="flex min-w-0 gap-1.5 overflow-x-auto">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cn(
            PICK,
            option.value === value
              ? 'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
              : 'border border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)] hover:bg-accent'
          )}
        >
          {option.label}
        </button>
      ))}
    </fieldset>
  )
}

/** What the paper is waiting on from the owner, as the pack counts and says it. */
export function needsYou(pack: TaxPack): string[] {
  const fmv = pack.capital_gains.needs_fmv.length
  const cost = pack.capital_gains.unknown_cost.length
  return [
    ...(fmv > 0 ? [`${fmv} ${fmv === 1 ? 'sale needs' : 'sales need'} the 31 Jan 2018 price`] : []),
    ...(cost > 0 ? [`${cost} ${cost === 1 ? 'sale has' : 'sales have'} no purchase record`] : []),
    ...pack.inputs.missing,
  ]
}

function Paper({
  pack,
  onRead,
  sheet,
  onSheet,
  onClose,
  dividends,
}: {
  pack: TaxPack
  onRead: () => void
  sheet: TaxSheet | null
  onSheet: (name: TaxSheet) => void
  onClose: () => void
  /** The Dividends card, in its place among the heads. */
  dividends: ReactNode
}) {
  const now = useNow(60_000)
  const meta = asOf(pack, now)
  const needs = needsYou(pack)
  return (
    <>
      <p className="flex items-start gap-1.5 text-xs font-medium text-[color:var(--text-tertiary)]">
        {meta.old && (
          <span className="inline-flex shrink-0 items-center gap-1 font-semibold text-[color:var(--warning-text)]">
            <Clock aria-hidden="true" className="size-3.5" />
            Old
          </span>
        )}
        <span>{meta.words}</span>
      </p>
      <ReadsCard pack={pack} onRead={onRead} />
      {needs.length > 0 && (
        <Card title="Needs you">
          <Sentences lines={needs} warn />
          <button type="button" onClick={() => onSheet('tax-inputs')} className={OUTLINE}>
            <PencilLine aria-hidden="true" className="size-4" />
            Open Inputs
          </button>
        </Card>
      )}
      <CapitalGainsCard cg={pack.capital_gains} onSchedule={() => onSheet('tax-sales')} />
      <BusinessCard business={pack.business} />
      <LossesCard pack={pack} />
      {dividends}
      <ChargesCard pack={pack} />
      <LeftOutCard pack={pack} />
      <Downloads pack={pack} />
      <ScheduleSheet
        open={sheet === 'tax-sales'}
        onClose={onClose}
        cg={pack.capital_gains}
        fyLabel={pack.fy_label}
      />
    </>
  )
}

export function TaxView({
  members,
  insights = false,
}: {
  members: readonly Member[]
  /** The insights switch: the Dividends card reads Upstox's corporate actions behind it. */
  insights?: boolean
}) {
  const current = fyOfDay(dayOfInstant(Date.now()))
  const [fy, setFy] = useState(current)
  const [member, setMember] = useState<number | undefined>(undefined)
  const query = useQuery({
    ...taxOptions(fy, member),
    placeholderData: keepPreviousData,
    // A read in progress is asked after again (the reader's progress, not market data).
    refetchInterval: (q) =>
      q.state.data && readingOf(q.state.data.inputs.sources).reading.length > 0
        ? READING_MS
        : false,
  })
  const sheets = useTaxSheet()
  const years = Array.from({ length: REPORT_YEARS }, (_, n) => current - n)
  const problem = query.error ? problemOf(query.error) : null
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="sd-display text-[1.375rem] leading-tight font-bold">Tax</h2>
        <button type="button" onClick={() => sheets.open('tax-inputs')} className={OUTLINE}>
          <PencilLine aria-hidden="true" className="size-4" />
          Inputs
        </button>
      </div>
      <Picker
        label="Financial year"
        options={years.map((year) => ({ value: year, label: fyLabel(year) }))}
        value={fy}
        onChange={setFy}
      />
      {members.length > 1 && (
        <Picker
          label="Taxpayer"
          options={members.map((m) => ({ value: m.id, label: m.name }))}
          value={member ?? members[0].id}
          onChange={setMember}
        />
      )}
      {problem !== null ? (
        <div className={cn(CARD, 'flex flex-col items-start gap-2')} aria-live="polite">
          <p className="text-sm text-[color:var(--text-secondary)]">
            {problem.code === 'not_ready' ? TAX_OFF : problem.message}
          </p>
          {problem.code !== 'not_ready' && (
            <button type="button" onClick={() => void query.refetch()} className={OUTLINE}>
              <RefreshCw aria-hidden="true" className="size-4" />
              Try again
            </button>
          )}
        </div>
      ) : query.data === undefined ? (
        <p className={cn(CARD, 'text-sm text-[color:var(--text-secondary)]')} aria-live="polite">
          Working out {fyLabel(fy)}.
        </p>
      ) : (
        <Paper
          pack={query.data}
          onRead={() => void query.refetch()}
          sheet={sheets.sheet}
          onSheet={sheets.open}
          onClose={sheets.close}
          dividends={<DividendsCard fy={fy} member={member} on={insights} />}
        />
      )}
      <InputsSheet
        open={sheets.sheet === 'tax-inputs'}
        onClose={sheets.close}
        pack={query.data}
        member={member}
        fy={fy}
      />
      <p
        role="note"
        className="sticky bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-10 flex items-start gap-2 rounded-[0.875rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-3 py-2 text-sm font-semibold text-[color:var(--warning-text)] md:bottom-2"
      >
        <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        {TAX_NOTICE}
      </p>
    </div>
  )
}
