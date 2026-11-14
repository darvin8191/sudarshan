// SUDARSHAN P7 F3 (M7.42, M15.5): the two parts of the Tax view that are the journal's (design
// p7 9.1): "Estimate against actual" under the Charges card, month by month from the journal's
// `charges_reconcile`, and the button that asks the journal to build the FY pack (its PDF is the
// tax working paper as the portfolio computed it). Both go through the journal's bridge; neither
// works a figure out here. The comparison is not market data: nothing says "Real-time".
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, CircleAlert, FileText } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { journalCall, problemOf } from '@/sudarshan/journal/api'
import type { ChargesReconcile, ReconcileMonth } from '@/sudarshan/journal/types'
import { rs } from './format'
import { TAX_KEY } from './keys'
import { BUTTON, OUTLINE, RULE } from './parts'

/** The block's sentence while the journal itself is switched off on the server. */
export const JOURNAL_OFF = 'Switch on the journal to compare estimates.'
export const RECONCILE_HELP =
  'The journal’s estimate of each month’s charges against Upstox’s own figure. A month is marked to check when the two differ by more than 5%.'

/** "FY2627" for the year starting in 2026: the journal's key for a tax pack. */
export function fyKey(fy: number): string {
  const two = (year: number) => String(year % 100).padStart(2, '0')
  return `FY${two(fy)}${two(fy + 1)}`
}

/**
 * A difference in words as well as sign: the actual less the estimate, so above zero Upstox
 * charged more than the journal estimated.
 */
export function differenceWords(paise: number): string {
  if (paise === 0) return 'Same as the estimate'
  return paise > 0 ? `${rs(paise)} more than estimated` : `${rs(-paise)} less than estimated`
}

function Month({ month }: { month: ReconcileMonth }) {
  return (
    <li className="flex flex-col gap-0.5 border-t border-[color:var(--divider)] py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold">{month.label}</span>
        {month.flagged && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-2 py-0.5 text-xs font-semibold text-[color:var(--warning-text)]">
            <CircleAlert aria-hidden="true" className="size-3.5" />
            Check
          </span>
        )}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm tabular-nums">
        <dt className="text-[color:var(--text-secondary)]">Estimate</dt>
        <dd className="text-right">{rs(month.estimate_paise)}</dd>
        <dt className="text-[color:var(--text-secondary)]">Upstox’s actual</dt>
        <dd className="text-right">
          {month.actual_paise === null ? 'Not read yet' : rs(month.actual_paise)}
        </dd>
        {month.diff_paise !== null && (
          <>
            <dt className="text-[color:var(--text-secondary)]">Difference</dt>
            <dd className="text-right font-semibold">{differenceWords(month.diff_paise)}</dd>
          </>
        )}
      </dl>
      {month.words && <p className="text-xs text-[color:var(--text-tertiary)]">{month.words}</p>}
    </li>
  )
}

/** The months that have orders, newest first; a year with none says so in the service's words. */
function Months({ data }: { data: ChargesReconcile }) {
  const months = data.months.filter((m) => m.orders > 0).reverse()
  if (months.length === 0) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        {data.note ??
          'The journal holds no trades of this year, so there is nothing to compare yet.'}
      </p>
    )
  }
  return (
    <>
      {data.year.actual_paise !== null && data.year.diff_paise !== null && (
        <p className="text-sm text-[color:var(--text-secondary)]">
          {data.year.months} {data.year.months === 1 ? 'month' : 'months'} compared so far: estimate{' '}
          {rs(data.year.estimate_paise)}, Upstox’s actual {rs(data.year.actual_paise)}.{' '}
          {differenceWords(data.year.diff_paise)}.
        </p>
      )}
      <ul aria-label="Estimate against actual, by month" className="flex flex-col">
        {months.map((month) => (
          <Month key={month.month} month={month} />
        ))}
      </ul>
    </>
  )
}

export function ReconcileBlock({ fy }: { fy: number }) {
  const query = useQuery({
    queryKey: [...TAX_KEY, 'reconcile', fy] as const,
    queryFn: ({ signal }) => journalCall('charges_reconcile', { fy }, signal),
    retry: false,
    refetchOnWindowFocus: false,
  })
  const result = query.data
  return (
    <div className={`${RULE} flex flex-col gap-2`}>
      <h3 className="text-sm font-semibold">Estimate against actual</h3>
      {query.isError ? (
        <p className="text-sm text-[color:var(--text-secondary)]" aria-live="polite">
          {problemOf(query.error).message}
        </p>
      ) : result === undefined ? (
        <p className="text-sm text-[color:var(--text-secondary)]" aria-live="polite">
          Reading the journal’s comparison.
        </p>
      ) : !result.enabled ? (
        <p className="text-sm text-[color:var(--text-secondary)]">{JOURNAL_OFF}</p>
      ) : (
        <>
          <p className="text-xs text-[color:var(--text-tertiary)]">{RECONCILE_HELP}</p>
          <Months data={result.data} />
        </>
      )}
    </div>
  )
}

/**
 * "Build the FY pack (PDF)": the journal builds the year's Tax pack from this working paper and
 * keeps it under Reports. One request at a time; the answer is the journal's own sentence.
 */
export function BuildPack({ fy }: { fy: number }) {
  const [state, setState] = useState<'idle' | 'building' | 'built' | 'failed'>('idle')
  const [words, setWords] = useState('')
  const build = async () => {
    if (state === 'building') return
    setState('building')
    try {
      const result = await journalCall('report_build', { kind: 'fy', period: fyKey(fy) })
      if (!result.enabled) {
        setWords('The journal is switched off on the server, so it cannot build the pack.')
        setState('failed')
        return
      }
      setWords(result.data.words ?? 'The tax pack is in the archive.')
      setState('built')
    } catch (error) {
      setWords(problemOf(error).message)
      setState('failed')
    }
  }
  return (
    <section aria-label="FY pack" className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => void build()}
        disabled={state === 'building'}
        className={OUTLINE}
      >
        <FileText aria-hidden="true" className="size-4 shrink-0" />
        {state === 'building' ? 'Building the FY pack' : 'Build the FY pack (PDF)'}
      </button>
      {state === 'built' && (
        <p className="flex flex-wrap items-center gap-2 text-sm" aria-live="polite">
          <span className="text-[color:var(--text-secondary)]">{words}</span>
          <Link to="/reports" className={`${BUTTON} text-[color:var(--brand)] underline`}>
            Open Reports
            <ArrowRight aria-hidden="true" className="size-4" />
          </Link>
        </p>
      )}
      {state === 'failed' && (
        <p className="text-sm font-semibold text-[color:var(--warning-text)]" aria-live="polite">
          {words}
        </p>
      )}
    </section>
  )
}
