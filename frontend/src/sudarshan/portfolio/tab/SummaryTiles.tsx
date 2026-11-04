// SUDARSHAN M7.23: widgets 1 and 2 of the Overview (design 4.6): the Summary and Today of a tab.
import { cn } from '@/lib/utils'
import { Signed } from '../Signed'
import { NAV_PENDING_NOTE, TODAY_WORDS } from '../statusWords'
import type { TabFixed, TabTotals } from '../types'
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

/** Value in Khand (the one large figure), what went in, and the overall gain and its parts. */
export function SummaryTile({
  totals,
  fixed,
  stale = false,
}: {
  totals: TabTotals
  fixed: TabFixed
  stale?: boolean
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
            <dd className={NOTE}>
              Net worth {rupeesText(totals.value_paise - fixed.liabilities_paise)}
            </dd>
          )}
        </div>
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
      {totals.unpriced > 0 && (
        <p className={NOTE}>
          {totals.unpriced} holding{totals.unpriced === 1 ? ' has' : 's have'} no price yet and{' '}
          {totals.unpriced === 1 ? 'is' : 'are'} counted at cost.
        </p>
      )}
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
