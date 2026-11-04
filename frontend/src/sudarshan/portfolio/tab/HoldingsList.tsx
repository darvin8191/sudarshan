// SUDARSHAN M7.23: widget 3 of the Overview (design 4.6): the holdings of a tab as cards on a
// phone and a table from 48rem. Every price says how old it is; "No price yet" and "not known
// yet" are words, never 0; a figure that can go negative carries its sign and arrow.
import { Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatAmount } from '@/sudarshan/dashboard/format'
import { pctNumber, Signed } from '../Signed'
import type { HoldingColumn } from '../types'
import { priceText, quantityText, rupeesText } from './format'
import type { ListedRow } from './rows'

const TH = 'px-3 py-2 text-left text-xs font-semibold text-muted-foreground'

function weightText(r: ListedRow): string {
  const n = pctNumber(r.weightPct)
  return n === null ? 'Not known yet' : `${formatAmount(n, 2)}%`
}

/** The price's age: "Old" is a clock and the word; everything else is the words alone. */
function Age({ r }: { r: ListedRow }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-xs',
        r.old
          ? 'font-semibold text-[color:var(--warning-text)]'
          : 'text-[color:var(--text-tertiary)]'
      )}
    >
      {r.old && (
        <>
          <Clock aria-hidden="true" className="size-3.5 shrink-0" />
          Old ·{' '}
        </>
      )}
      {r.words}
    </span>
  )
}

function PriceCell({ r }: { r: ListedRow }) {
  return (
    <span className="flex flex-col">
      <span className="font-semibold">
        {r.row.price.price_u === null ? 'No price yet' : priceText(r.row.price.price_u)}
      </span>
      <Age r={r} />
    </span>
  )
}

function TodayCell({ r, stale }: { r: ListedRow; stale: boolean }) {
  const today = r.figures.today
  if (today.state === 'ok' && today.paise !== null) {
    return <Signed paise={today.paise} pct={today.pct} stale={stale} />
  }
  const words = {
    unknown: 'Not known yet',
    pending: 'Waiting for the NAV',
    none: 'No Today for this holding',
    ok: '',
  }[today.state]
  return <span className="text-[color:var(--text-secondary)]">{words}</span>
}

function GainCell({ r, stale }: { r: ListedRow; stale: boolean }) {
  if (!r.figures.priced)
    return <span className="text-[color:var(--text-secondary)]">No price yet</span>
  return <Signed paise={r.figures.unrealised_paise} pct={r.figures.gain_pct} stale={stale} />
}

function Card({ r, stale }: { r: ListedRow; stale: boolean }) {
  const asset = r.row.asset
  return (
    <li className="flex flex-col gap-2 rounded-[1.125rem] border bg-card px-4 py-3 shadow-[var(--shadow-card)]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-base font-bold">{asset.name}</p>
          <p className="text-sm text-[color:var(--text-secondary)]">
            {quantityText(r.qtyU)}
            {asset.unit_label ? ` ${asset.unit_label}` : ''}
            {r.avgCostU === null ? '' : ` · avg ${priceText(r.avgCostU)}`}
          </p>
        </div>
        <div className="text-right text-sm">
          <PriceCell r={r} />
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <div className="flex flex-col">
          <dt className="text-xs font-semibold text-muted-foreground">Today</dt>
          <dd>
            <TodayCell r={r} stale={stale} />
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-xs font-semibold text-muted-foreground">Gain</dt>
          <dd>
            <GainCell r={r} stale={stale} />
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-xs font-semibold text-muted-foreground">Value</dt>
          <dd className="font-semibold">{rupeesText(r.figures.value_paise)}</dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-xs font-semibold text-muted-foreground">Weight</dt>
          <dd className="font-semibold">{weightText(r)}</dd>
        </div>
      </dl>
    </li>
  )
}

const HEADERS: Record<HoldingColumn, string> = {
  name: 'Name',
  quantity: 'Quantity',
  avg_cost: 'Average cost',
  price: 'Price',
  today: 'Today',
  value: 'Value',
  gain: 'Gain',
  weight: 'Weight',
}

function Cell({ column, r, stale }: { column: HoldingColumn; r: ListedRow; stale: boolean }) {
  switch (column) {
    case 'name':
      return <span className="font-bold">{r.row.asset.name}</span>
    case 'quantity':
      return <>{quantityText(r.qtyU)}</>
    case 'avg_cost':
      return <>{r.avgCostU === null ? 'Not known yet' : priceText(r.avgCostU)}</>
    case 'price':
      return <PriceCell r={r} />
    case 'today':
      return <TodayCell r={r} stale={stale} />
    case 'value':
      return <span className="font-semibold">{rupeesText(r.figures.value_paise)}</span>
    case 'gain':
      return <GainCell r={r} stale={stale} />
    case 'weight':
      return <>{weightText(r)}</>
  }
}

export function HoldingsList({
  rows,
  columns,
  stale,
}: {
  rows: readonly ListedRow[]
  columns: readonly HoldingColumn[]
  /** Whether a row should be moving and is not: shown greyed, sign and arrow kept. */
  stale: (r: ListedRow) => boolean
}) {
  return (
    <section aria-label="Holdings" className="flex flex-col gap-2">
      <ul aria-label="Holdings cards" className="flex flex-col gap-2 md:hidden">
        {rows.map((r) => (
          <Card key={r.row.asset.id} r={r} stale={stale(r)} />
        ))}
      </ul>
      <div className="hidden overflow-x-auto rounded-[1.125rem] border bg-card shadow-[var(--shadow-card)] md:block">
        <table className="w-full min-w-max text-sm">
          <thead className="border-b border-[color:var(--divider)]">
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col" className={TH}>
                  {HEADERS[c]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--divider)]">
            {rows.map((r) => (
              <tr key={r.row.asset.id} className="min-h-12 align-top">
                {columns.map((c) => (
                  <td key={c} className="px-3 py-3">
                    <Cell column={c} r={r} stale={stale(r)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
