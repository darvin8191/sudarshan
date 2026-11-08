// SUDARSHAN M7.23: widgets 1 and 2 of the Overview (design 4.6): the Summary and Today of a tab.
// A tab with a loan (M7.19 V2) also shows net worth and each loan's balance with its age.
import { Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Signed } from '../Signed'
import { NAV_PENDING_NOTE, TODAY_WORDS } from '../statusWords'
import type { Liability, TabFixed, TabTotals } from '../types'
import { rupeesText } from './format'

const TILE = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const LABEL = 'text-xs font-semibold text-muted-foreground'
const NOTE = 'text-[0.78125rem] font-medium text-[color:var(--text-secondary)]'

function Part({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-px">
      <dt className={LABEL}>{label}</dt>
      <dd className="text-[0.9375rem] font-semibold">{children}</dd>
    </div>
  )
}

/** Holdings with no price yet are counted at cost: said wherever a figure rests on that. */
export function UnpricedNote({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <p className={NOTE}>
      {count} holding{count === 1 ? ' has' : 's have'} no price yet and {count === 1 ? 'is' : 'are'}{' '}
      counted at cost.
    </p>
  )
}

/** The tab's loans: each balance, its date and its age ("Old" is a clock and the word). */
export function Loans({
  loans,
  onUpdate,
}: {
  loans: readonly Liability[]
  /** Opens the balance sheet for a loan; without it the list is read-only. */
  onUpdate?: (loan: Liability) => void
}) {
  if (loans.length === 0) return null
  return (
    <ul aria-label="Loans" className="flex flex-col divide-y divide-[color:var(--divider)]">
      {loans.map((loan) => {
        const old = loan.fresh.state === 'old'
        return (
          <li key={loan.asset_id} className="flex items-center gap-3 py-1.5">
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-[0.9375rem] font-semibold">{loan.name}</span>
              <span
                className={cn(
                  'inline-flex items-center gap-1 text-xs',
                  old
                    ? 'font-semibold text-[color:var(--warning-text)]'
                    : 'text-[color:var(--text-tertiary)]'
                )}
              >
                {old && (
                  <>
                    <Clock aria-hidden="true" className="size-3.5 shrink-0" />
                    Old ·{' '}
                  </>
                )}
                {loan.fresh.words}
              </span>
            </div>
            <span className="text-[0.9375rem] font-semibold tabular-nums">
              {rupeesText(loan.outstanding_paise)}
            </span>
            {onUpdate && (
              <button
                type="button"
                onClick={() => onUpdate(loan)}
                className="inline-flex min-h-12 min-w-12 items-center justify-center rounded-[0.875rem] border border-[color:var(--border-strong)] px-3 text-sm font-semibold text-[color:var(--brand)] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                Update
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Value in Khand (the one large figure), what went in, and the overall gain and its parts. */
export function SummaryTile({
  totals,
  fixed,
  stale = false,
  loans = [],
  onUpdateLoan,
}: {
  totals: TabTotals
  fixed: TabFixed
  stale?: boolean
  loans?: readonly Liability[]
  onUpdateLoan?: (loan: Liability) => void
}) {
  return (
    <section aria-label="Summary" className={cn(TILE, 'flex flex-col gap-3')}>
      <dl className="flex flex-col gap-3">
        <div className="flex flex-col gap-px">
          <dt className={LABEL}>Value</dt>
          <dd
            className={cn(
              'sd-display text-[2rem] leading-[1.1] font-semibold',
              stale && 'text-muted-foreground'
            )}
          >
            {rupeesText(totals.value_paise)}
          </dd>
          <dd className={NOTE}>Invested in current holdings {rupeesText(totals.cost_paise)}</dd>
          {fixed.liabilities_paise > 0 && (
            <dd className={cn(NOTE, 'text-foreground')}>
              Net worth{' '}
              <span className="font-semibold">
                {rupeesText(totals.value_paise - fixed.liabilities_paise)}
              </span>{' '}
              after loans of {rupeesText(fixed.liabilities_paise)}
            </dd>
          )}
        </div>
        {loans.length > 0 && (
          <div className="flex flex-col gap-px border-t border-[color:var(--divider)] pt-3">
            <dt className={LABEL}>Loans</dt>
            <dd>
              <Loans loans={loans} onUpdate={onUpdateLoan} />
            </dd>
          </div>
        )}
        <div className="flex flex-col gap-px">
          <dt className={LABEL}>Overall gain</dt>
          <dd className="text-[1.0625rem] font-bold">
            <Signed
              paise={totals.overall_gain_paise}
              pct={totals.absolute_return_pct}
              stale={stale}
            />
          </dd>
          <dd className="sr-only">Absolute return is the percentage beside it.</dd>
        </div>
        <div className="grid grid-cols-2 gap-3 border-t border-[color:var(--divider)] pt-3">
          <Part label="Unrealised">
            <Signed paise={totals.unrealised_paise} stale={stale} />
          </Part>
          <Part label="Realised">
            <Signed paise={fixed.realised_paise} />
          </Part>
          <Part label="Income">
            <Signed paise={fixed.income_paise} />
          </Part>
          <Part label="Fees">
            <Signed paise={-fixed.fees_paise} />
          </Part>
        </div>
      </dl>
      <UnpricedNote count={totals.unpriced} />
    </section>
  )
}

/** Today's change of the tab, or the words for why there is none. Never a zero in their place. */
export function TodayTile({
  totals,
  fixed,
  stale = false,
}: {
  totals: TabTotals
  fixed: TabFixed
  stale?: boolean
}) {
  const today = totals.today
  return (
    <section aria-label="Today" className={cn(TILE, 'flex flex-col gap-1.5')}>
      <h2 className={LABEL}>Today</h2>
      {today.state === 'ok' && today.paise !== null ? (
        <p className="text-[1.0625rem] font-bold">
          <Signed paise={today.paise} pct={today.pct} stale={stale} />
        </p>
      ) : (
        <p className="text-base font-semibold text-[color:var(--text-secondary)]">
          {today.state === 'pending'
            ? TODAY_WORDS.pending
            : today.state === 'none'
              ? 'Nothing here has a Today figure.'
              : TODAY_WORDS.unknown}
        </p>
      )}
      {today.state === 'ok' && today.unknown > 0 && (
        <p className={NOTE}>
          {today.unknown} holding{today.unknown === 1 ? ' is' : 's are'} not in this figure: no
          previous close yet.
        </p>
      )}
      {today.pending > 0 && <p className={NOTE}>{NAV_PENDING_NOTE}</p>}
      {fixed.closed_today_paise === null && (
        <p className={NOTE}>A holding sold today is not in this figure yet.</p>
      )}
    </section>
  )
}
