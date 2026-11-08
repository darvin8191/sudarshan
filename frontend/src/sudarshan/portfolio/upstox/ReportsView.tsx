// SUDARSHAN M7.6, M7.7: the Upstox tab's Trades and P&L views. Upstox's trade history and its
// trade-wise realised P&L with the charges, per financial year (the last three) and segment. This
// is not market data: every figure carries the time the service read it from Upstox ("As of"), a
// past year is read once, and the current year at most once a night or when this view finds it
// older than six hours. Loading, empty and refused states are each a sentence.
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { PortfolioError, portfolioCall, problemOf } from '../api'
import { clockOfInstant } from '../days'
import { Signed } from '../Signed'
import { SECONDARY } from '../sheets/TabFields'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { dayText, priceText, quantityText, rupeesText } from '../tab/format'
import {
  type ChargesBreakdown,
  type PnlReport,
  REPORT_SEGMENTS,
  type ReportPart,
  type ReportSegment,
  type ReportYears,
  type TradesReport,
} from '../types'
import { CARD, CHIP } from './parts'

export const SEGMENT_LABELS: Record<ReportSegment, string> = {
  EQ: 'Equity',
  FO: 'F&O',
  COM: 'Commodity',
  CD: 'Currency',
  MF: 'Mutual funds',
}

/** How often the view asks again while the service is still reading from Upstox. */
export const READING_POLL_MS = 3000

const LABEL = 'text-xs font-semibold text-muted-foreground'
const CHOICE =
  'inline-flex min-h-12 items-center rounded-full border px-4 text-[0.9375rem] whitespace-nowrap outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50'

function Choices<V extends string | number>({
  label,
  values,
  text,
  value,
  onChange,
}: {
  label: string
  values: readonly V[]
  text: (v: V) => string
  value: V
  onChange: (v: V) => void
}) {
  return (
    <fieldset className="m-0 flex min-w-0 gap-1.5 overflow-x-auto border-0 p-0 pb-1">
      <legend className="sr-only">{label}</legend>
      {values.map((v) => (
        <button
          key={String(v)}
          type="button"
          aria-pressed={v === value}
          onClick={() => onChange(v)}
          className={cn(
            CHOICE,
            v === value
              ? 'border-2 border-[color:var(--brand)] bg-[color:var(--brand-tint)] font-bold text-[color:var(--brand)]'
              : 'border-[color:var(--border-strong)] bg-card font-medium text-[color:var(--text-secondary)] hover:bg-accent'
          )}
        >
          {text(v)}
        </button>
      ))}
    </fieldset>
  )
}

/** "As of 21:05:12" (the service's read), or that nothing has been read yet. */
export function AsOf({ part }: { part: ReportPart }) {
  return (
    <span className="text-xs font-semibold text-[color:var(--text-tertiary)]">
      {part.read_at === null ? 'Not read from Upstox yet' : `As of ${clockOfInstant(part.read_at)}`}
    </span>
  )
}

/** The sentence a part carries when it is not complete: reading, partial or not available. */
export const PART_WORDS: Record<'reading' | 'partial' | 'unavailable', string> = {
  reading: 'Upstox is still answering. This page refreshes in a moment.',
  partial:
    'Some pages are not read yet, so the figures so far are shown. Tap Read more to carry on.',
  unavailable: 'Upstox did not give this report. Try again in a few minutes.',
}

export function PartLine({ part }: { part: ReportPart }) {
  if (part.state === 'ok' && !part.note) return null
  const reading = part.state === 'reading'
  const words = part.note ?? PART_WORDS[part.state as 'reading' | 'partial' | 'unavailable']
  return (
    <p
      aria-live="polite"
      className={cn(
        'flex items-start gap-2 rounded-[0.875rem] border px-3 py-2 text-sm font-medium',
        reading
          ? 'border-[color:var(--divider)] text-[color:var(--text-secondary)]'
          : 'border-[color:var(--warning-text)] bg-[color:var(--gold-tint)] text-[color:var(--warning-text)]'
      )}
    >
      {reading ? (
        <LoaderCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 animate-spin" />
      ) : (
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      )}
      {words}
    </p>
  )
}

/** A partial year: reading again carries on from the page the service stopped at. */
export function ReadMore({
  part,
  busy,
  onMore,
}: {
  part: ReportPart
  busy: boolean
  onMore: () => void
}) {
  if (part.state !== 'partial') return null
  return (
    <button type="button" className={SECONDARY} onClick={onMore} disabled={busy}>
      <RefreshCw aria-hidden="true" className={cn('size-4', busy && 'animate-spin')} />
      Read more
    </button>
  )
}

function Waiting({ what }: { what: string }) {
  return (
    <p
      className="flex items-center justify-center gap-2 py-8 text-sm text-[color:var(--text-secondary)]"
      aria-live="polite"
    >
      <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
      Reading your {what} from Upstox
    </p>
  )
}

function Failed({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div
      className={`${CARD} flex flex-col items-center gap-3 px-6 py-8 text-center`}
      aria-live="polite"
    >
      <TriangleAlert aria-hidden="true" className="size-6 text-[color:var(--text-secondary)]" />
      <p className="text-base font-bold">{problemOf(error).message}</p>
      <button type="button" className={SECONDARY} onClick={retry}>
        <RefreshCw aria-hidden="true" className="size-4" />
        Try again
      </button>
    </div>
  )
}

function useYears() {
  return useQuery({
    queryKey: [...PORTFOLIO_KEY, 'report-years'],
    queryFn: async (): Promise<ReportYears> => {
      const result = await portfolioCall('report_years', {})
      if (!result.enabled)
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      return result.data
    },
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
}

function useReport<K extends 'report_trades' | 'report_pnl'>(
  op: K,
  fy: number | null,
  segment: ReportSegment
) {
  return useQuery({
    queryKey: [...PORTFOLIO_KEY, op, fy, segment],
    enabled: fy !== null,
    queryFn: async () => {
      const result = await portfolioCall(op, { fy: fy as number, segment })
      if (!result.enabled)
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      return result.data as K extends 'report_trades' ? TradesReport : PnlReport
    },
    placeholderData: keepPreviousData,
    retry: false,
    refetchOnWindowFocus: false,
    // Not market data: the only timer here follows a read the service has not finished.
    refetchInterval: (query) => (query.state.data?.state === 'reading' ? READING_POLL_MS : false),
  })
}

/** The year and segment choosers shared by both views; the year list comes from the service. */
function Pickers({
  years,
  fy,
  onFy,
  segment,
  onSegment,
  segments,
}: {
  years: ReportYears
  fy: number
  onFy: (fy: number) => void
  segment: ReportSegment
  onSegment: (s: ReportSegment) => void
  segments: readonly ReportSegment[]
}) {
  const label = (v: number) => {
    const year = years.years.find((y) => y.fy === v)
    return year ? `${year.label}${year.current ? ' (this year)' : ''}` : String(v)
  }
  return (
    <div className="flex flex-col gap-2">
      <Choices
        label="Financial year"
        values={years.years.map((y) => y.fy)}
        text={label}
        value={fy}
        onChange={onFy}
      />
      <Choices
        label="Segment"
        values={segments}
        text={(s) => SEGMENT_LABELS[s]}
        value={segment}
        onChange={onSegment}
      />
    </div>
  )
}

function usePick(years: ReportYears | undefined) {
  const [fy, setFy] = useState<number | null>(null)
  const [segment, setSegment] = useState<ReportSegment>('EQ')
  const chosen = fy ?? years?.years[0]?.fy ?? null
  return { fy: chosen, setFy, segment, setSegment }
}

// ---- Trades --------------------------------------------------------------------------------------

/** BUY or SELL as the word, in the buy or sell colour. */
function SideChip({ side }: { side: 'BUY' | 'SELL' }) {
  return (
    <span
      className={cn(
        CHIP,
        side === 'BUY'
          ? 'border-[color:var(--buy-solid)] text-[color:var(--buy-solid)]'
          : 'border-[color:var(--sell-solid)] text-[color:var(--sell-solid)]'
      )}
    >
      {side}
    </span>
  )
}

export function TradesList({ report }: { report: TradesReport }) {
  if (report.rows.length === 0) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        {report.state === 'ok'
          ? `No trades in ${SEGMENT_LABELS[report.segment]} in this financial year.`
          : 'No trades to show yet.'}
      </p>
    )
  }
  return (
    <>
      <ul
        aria-label="Trades"
        className="flex flex-col divide-y divide-[color:var(--divider)] md:hidden"
      >
        {report.rows.map((row) => (
          <li key={`${row.trade_id}-${row.trade_date}`} className="flex flex-col gap-0.5 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-[0.9375rem] font-semibold">
                {row.scrip_name}
              </span>
              <span className="text-[0.9375rem] font-semibold tabular-nums">
                {rupeesText(row.amount_paise)}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[color:var(--text-secondary)]">
              <SideChip side={row.transaction_type} />
              <span>{dayText(row.trade_date)}</span>
              <span>
                {quantityText(row.qty_u)} at {priceText(row.price_u)}
              </span>
              {row.option_type && row.strike_price_u !== null && (
                <span>
                  {priceText(row.strike_price_u)} {row.option_type}
                  {row.expiry ? `, expiring ${dayText(row.expiry)}` : ''}
                </span>
              )}
              <span>{row.exchange}</span>
            </div>
          </li>
        ))}
      </ul>
      <table aria-label="Trades table" className="hidden w-full text-sm md:table">
        <thead>
          <tr className="text-left text-xs font-semibold text-muted-foreground">
            <th className="py-2 pr-3 font-semibold">Date</th>
            <th className="py-2 pr-3 font-semibold">Instrument</th>
            <th className="py-2 pr-3 font-semibold">Side</th>
            <th className="py-2 pr-3 text-right font-semibold">Quantity</th>
            <th className="py-2 pr-3 text-right font-semibold">Price</th>
            <th className="py-2 text-right font-semibold">Amount</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[color:var(--divider)]">
          {report.rows.map((row) => (
            <tr key={`${row.trade_id}-${row.trade_date}`}>
              <td className="py-2 pr-3 whitespace-nowrap">{dayText(row.trade_date)}</td>
              <td className="py-2 pr-3 font-semibold">
                {row.scrip_name}
                {row.option_type && row.strike_price_u !== null && (
                  <span className="block text-xs font-normal text-[color:var(--text-secondary)]">
                    {priceText(row.strike_price_u)} {row.option_type}
                    {row.expiry ? `, expiring ${dayText(row.expiry)}` : ''}
                  </span>
                )}
              </td>
              <td className="py-2 pr-3">
                <SideChip side={row.transaction_type} />
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{quantityText(row.qty_u)}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{priceText(row.price_u)}</td>
              <td className="py-2 text-right font-semibold tabular-nums">
                {rupeesText(row.amount_paise)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function TradesView() {
  const years = useYears()
  const pick = usePick(years.data)
  const report = useReport('report_trades', pick.fy, pick.segment)
  if (years.isPending) return <Waiting what="trades" />
  if (years.isError || !years.data)
    return <Failed error={years.error} retry={() => years.refetch()} />
  const data = report.data as TradesReport | undefined
  return (
    <div className="flex flex-col gap-3">
      <Pickers
        years={years.data}
        fy={pick.fy as number}
        onFy={pick.setFy}
        segment={pick.segment}
        onSegment={pick.setSegment}
        segments={REPORT_SEGMENTS}
      />
      {report.isPending && !data ? (
        <Waiting what="trades" />
      ) : report.isError && !data ? (
        <Failed error={report.error} retry={() => report.refetch()} />
      ) : data ? (
        <section aria-label="Trade history" className={`${CARD} flex flex-col gap-3 px-4 py-3.5`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-bold">Trades</h2>
            <AsOf part={data} />
          </div>
          <PartLine part={data} />
          <dl className="grid grid-cols-3 gap-3">
            <div className="flex flex-col gap-px">
              <dt className={LABEL}>Trades</dt>
              <dd className="text-[0.9375rem] font-semibold">
                {data.total_records ?? data.rows.length}
              </dd>
            </div>
            <div className="flex flex-col gap-px">
              <dt className={LABEL}>Bought</dt>
              <dd className="text-[0.9375rem] font-semibold">{rupeesText(data.bought_paise)}</dd>
            </div>
            <div className="flex flex-col gap-px">
              <dt className={LABEL}>Sold</dt>
              <dd className="text-[0.9375rem] font-semibold">{rupeesText(data.sold_paise)}</dd>
            </div>
          </dl>
          <TradesList report={data} />
          <ReadMore part={data} busy={report.isFetching} onMore={() => report.refetch()} />
        </section>
      ) : null}
    </div>
  )
}

// ---- P&L -------------------------------------------------------------------------------------------

const CHARGE_LINES: Array<[keyof ChargesBreakdown, string]> = [
  ['brokerage_paise', 'Brokerage'],
  ['stt_paise', 'STT'],
  ['gst_paise', 'GST'],
  ['stamp_duty_paise', 'Stamp duty'],
  ['transaction_paise', 'Exchange transaction charges'],
  ['clearing_paise', 'Clearing charges'],
  ['sebi_turnover_paise', 'SEBI turnover fee'],
  ['ipft_paise', 'Investor protection fund'],
  ['demat_transaction_paise', 'Demat transaction charges'],
  ['others_paise', 'Other charges'],
]

/** The charges Upstox levied on the year's trades, each line labelled; one it did not give says so. */
export function Charges({ charges }: { charges: ChargesBreakdown | null }) {
  if (charges === null) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        Charges have not been read from Upstox yet.
      </p>
    )
  }
  return (
    <dl aria-label="Charges" className="flex flex-col gap-px text-sm">
      {CHARGE_LINES.map(([key, label]) => (
        <div key={key} className="flex items-center justify-between gap-2">
          <dt className="text-[color:var(--text-secondary)]">{label}</dt>
          <dd className="font-semibold tabular-nums">
            {charges[key] === null ? (
              <span className="font-medium text-[color:var(--text-tertiary)]">not given</span>
            ) : (
              rupeesText(charges[key] as number)
            )}
          </dd>
        </div>
      ))}
      <div className="flex items-center justify-between gap-2 border-t border-[color:var(--divider)] pt-1">
        <dt className="font-semibold">Total charges</dt>
        <dd className="font-bold tabular-nums">
          {charges.total_paise === null ? 'not given' : rupeesText(charges.total_paise)}
        </dd>
      </div>
    </dl>
  )
}

export function PnlList({ report }: { report: PnlReport }) {
  if (report.rows.length === 0) {
    return (
      <p className="text-sm text-[color:var(--text-secondary)]">
        {report.state === 'ok' && !report.note
          ? `No closed trades in ${SEGMENT_LABELS[report.segment]} in this financial year.`
          : report.note && report.state === 'ok'
            ? null
            : 'No closed trades to show yet.'}
      </p>
    )
  }
  return (
    <>
      <ul
        aria-label="Closed trades"
        className="flex flex-col divide-y divide-[color:var(--divider)] md:hidden"
      >
        {report.rows.map((row, n) => (
          <li
            key={`${row.isin ?? row.scrip_name}-${row.buy_date}-${row.sell_date}-${n}`}
            className="flex flex-col gap-0.5 py-2"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate text-[0.9375rem] font-semibold">
                {row.scrip_name}
              </span>
              <span className="text-[0.9375rem] font-semibold">
                <Signed paise={row.pnl_paise} />
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[color:var(--text-secondary)]">
              <span className={CHIP}>{row.trade_type}</span>
              <span>{quantityText(row.qty_u)}</span>
              <span>
                Bought {row.buy_date ? dayText(row.buy_date) : 'on a day not given'} at{' '}
                {priceText(row.buy_average_u)}
              </span>
              <span>
                Sold {row.sell_date ? dayText(row.sell_date) : 'on a day not given'} at{' '}
                {priceText(row.sell_average_u)}
              </span>
            </div>
          </li>
        ))}
      </ul>
      <table aria-label="Closed trades table" className="hidden w-full text-sm md:table">
        <thead>
          <tr className="text-left text-xs font-semibold text-muted-foreground">
            <th className="py-2 pr-3 font-semibold">Instrument</th>
            <th className="py-2 pr-3 text-right font-semibold">Quantity</th>
            <th className="py-2 pr-3 font-semibold">Bought</th>
            <th className="py-2 pr-3 text-right font-semibold">Buy average</th>
            <th className="py-2 pr-3 font-semibold">Sold</th>
            <th className="py-2 pr-3 text-right font-semibold">Sell average</th>
            <th className="py-2 text-right font-semibold">P&amp;L</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[color:var(--divider)]">
          {report.rows.map((row, n) => (
            <tr key={`${row.isin ?? row.scrip_name}-${row.buy_date}-${row.sell_date}-${n}`}>
              <td className="py-2 pr-3 font-semibold">{row.scrip_name}</td>
              <td className="py-2 pr-3 text-right tabular-nums">{quantityText(row.qty_u)}</td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {row.buy_date ? dayText(row.buy_date) : 'Not given'}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{priceText(row.buy_average_u)}</td>
              <td className="py-2 pr-3 whitespace-nowrap">
                {row.sell_date ? dayText(row.sell_date) : 'Not given'}
              </td>
              <td className="py-2 pr-3 text-right tabular-nums">{priceText(row.sell_average_u)}</td>
              <td className="py-2 text-right font-semibold">
                <Signed paise={row.pnl_paise} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

export function PnlView() {
  const years = useYears()
  const pick = usePick(years.data)
  const report = useReport('report_pnl', pick.fy, pick.segment)
  if (years.isPending) return <Waiting what="P&L" />
  if (years.isError || !years.data)
    return <Failed error={years.error} retry={() => years.refetch()} />
  const data = report.data as PnlReport | undefined
  return (
    <div className="flex flex-col gap-3">
      <Pickers
        years={years.data}
        fy={pick.fy as number}
        onFy={pick.setFy}
        segment={pick.segment}
        onSegment={pick.setSegment}
        segments={REPORT_SEGMENTS}
      />
      {report.isPending && !data ? (
        <Waiting what="P&L" />
      ) : report.isError && !data ? (
        <Failed error={report.error} retry={() => report.refetch()} />
      ) : data ? (
        <>
          <section aria-label="Realised P&L" className={`${CARD} flex flex-col gap-3 px-4 py-3.5`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-bold">Realised P&amp;L</h2>
              <AsOf part={data} />
            </div>
            <PartLine part={data} />
            <dl className="flex flex-col gap-3">
              <div className="flex flex-col gap-px">
                <dt className={LABEL}>Before charges</dt>
                <dd className="sd-display text-[1.5rem] leading-[1.1] font-semibold">
                  <Signed paise={data.realised_paise} />
                </dd>
                <dd className="text-xs text-[color:var(--text-tertiary)]">
                  {data.trades_count === null
                    ? 'Upstox has not said how many trades yet.'
                    : `${data.trades_count} closed trade${data.trades_count === 1 ? '' : 's'} as Upstox pairs them.`}
                </dd>
              </div>
              <div className="flex flex-col gap-px border-t border-[color:var(--divider)] pt-3">
                <dt className={LABEL}>After charges</dt>
                <dd className="text-[1.0625rem] font-bold">
                  {data.net_paise === null ? (
                    <span className="font-semibold text-[color:var(--text-secondary)]">
                      Not known until the charges are read.
                    </span>
                  ) : (
                    <Signed paise={data.net_paise} />
                  )}
                </dd>
              </div>
            </dl>
            <Charges charges={data.charges} />
          </section>
          <section
            aria-label="Closed trades list"
            className={`${CARD} flex flex-col gap-2 px-4 py-3.5`}
          >
            <h2 className="text-base font-bold">Closed trades</h2>
            <PnlList report={data} />
            <ReadMore part={data} busy={report.isFetching} onMore={() => report.refetch()} />
          </section>
        </>
      ) : null}
    </div>
  )
}
