// SUDARSHAN P7 F1 (M7.27): the year's schedule of sales as a sheet (design p7 9.1): one row per
// sale, or per part of one taken from one purchase, as the tax core worked it out, under the share's
// name with its ISIN below (the ISIN alone where no record names the share). A sale left out
// of the totals is listed with the reason in words. Long or short is a chip with a clock and the
// word; a gain or a loss carries its sign and the word.
import { Clock, Info } from 'lucide-react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { dayText, quantityText } from '../tab/format'
import type { CapitalGains, TaxGain } from '../types'
import { rs } from './format'
import { Result } from './parts'

const LEFT_OUT: Record<Exclude<TaxGain['status'], 'ok'>, string> = {
  needs_fmv: 'Needs the 31 Jan 2018 price. In no total until you add it.',
  unknown_cost: 'No purchase record. In no total.',
  exempt_buyback: 'A buyback that was exempt on its date. In no total.',
}
const TAG =
  'inline-flex items-center gap-1 rounded-full border border-[color:var(--border-strong)] px-2 py-0.5 text-xs font-semibold text-[color:var(--text-secondary)]'

function Sale({ row }: { row: TaxGain }) {
  return (
    <li className="flex flex-col gap-1 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex min-w-0 flex-col">
          <span className="text-[0.9375rem] font-semibold break-words">{row.name || row.isin}</span>
          {row.name && (
            <span className="text-xs break-all text-[color:var(--text-tertiary)]">{row.isin}</span>
          )}
        </span>
        <span className="shrink-0 whitespace-nowrap">
          {row.gain_paise === null ? 'Not worked out' : <Result paise={row.gain_paise} />}
        </span>
      </div>
      <span className="text-sm text-[color:var(--text-secondary)]">
        {quantityText(row.qty_u)} sold {dayText(row.sale_day)} for {rs(row.sale_paise)}
        {row.acquired_day !== null && ` · bought ${dayText(row.acquired_day)}`}
        {row.cost_paise !== null && ` for ${rs(row.cost_paise)}`}
        {row.expenses_paise > 0 &&
          ` · charges ${rs(row.expenses_paise)}${row.allocated ? ' (allocated)' : ''}`}
      </span>
      <span className="flex flex-wrap gap-1.5">
        {row.term !== null && (
          <span className={TAG}>
            <Clock aria-hidden="true" className="size-3.5" />
            {row.term === 'long' ? 'Long-term' : 'Short-term'}
          </span>
        )}
        {row.fmv_used && <span className={TAG}>31 Jan 2018 price used</span>}
        {row.buyback !== null && <span className={TAG}>Buyback</span>}
      </span>
      {row.status !== 'ok' && (
        <span className="flex gap-1.5 text-sm font-semibold text-[color:var(--warning-text)]">
          <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {LEFT_OUT[row.status]}
        </span>
      )}
    </li>
  )
}

export function ScheduleSheet({
  open,
  onClose,
  cg,
  fyLabel,
}: {
  open: boolean
  onClose: () => void
  cg: CapitalGains
  fyLabel: string
}) {
  const rows = [...cg.schedule, ...cg.needs_fmv, ...cg.unknown_cost, ...cg.exempt_buybacks]
  return (
    <BottomSheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title="Sales of the year"
      description={`${fyLabel} · working paper, verify with your CA`}
    >
      {rows.length === 0 ? (
        <p className="py-4 text-sm text-[color:var(--text-secondary)]">
          No sale of a share or an equity fund is recorded for this year.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
          {rows.map((row, n) => (
            <Sale key={n} row={row} />
          ))}
        </ul>
      )}
    </BottomSheet>
  )
}
