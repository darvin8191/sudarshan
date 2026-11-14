// SUDARSHAN P7 F1 (M7.27): the rest of the tax working paper (design p7 9.1): the set-off as the
// service's sentences, Upstox's own charges per segment, what the paper leaves out, the reads of
// Upstox it rests on, and the CSV of each schedule.
import { Download, RefreshCw } from 'lucide-react'
import { getTaxCsv, portfolioCall, saveDownload } from '../api'
import { useSend } from '../sheets/useSend'
import { dayText } from '../tab/format'
import type { ReportSegment, TaxPack, TaxSchedule, TaxSource } from '../types'
import { rs } from './format'
import { Card, Line, OUTLINE, RULE, Sentences } from './parts'

export const SEGMENT_WORDS: Record<ReportSegment, string> = {
  EQ: 'Shares',
  FO: 'F&O',
  COM: 'Commodity',
  CD: 'Currency',
  MF: 'Funds',
}

export function LossesCard({ pack }: { pack: TaxPack }) {
  const lines = [...pack.capital_gains.set_off_words, ...pack.business.set_off_words]
  return (
    <Card title="Losses">
      {lines.length === 0 ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          No loss was set off or carried in this year.
        </p>
      ) : (
        <Sentences lines={lines} />
      )}
    </Card>
  )
}

export function ChargesCard({ pack }: { pack: TaxPack }) {
  const rows = Object.entries(pack.charges.by_segment) as [
    ReportSegment,
    { total_paise: number | null; stt_paise: number | null },
  ][]
  return (
    <Card title="Charges">
      {rows.length === 0 ? (
        <p className="text-sm text-[color:var(--text-secondary)]">
          Upstox's charges for this year are not read yet. Open P&L on the Upstox tab to read them.
        </p>
      ) : (
        rows.map(([segment, charges]) => (
          <Line
            key={segment}
            label={SEGMENT_WORDS[segment]}
            sub={charges.stt_paise === null ? undefined : `of which STT ${rs(charges.stt_paise)}`}
          >
            {charges.total_paise === null ? 'Not given' : rs(charges.total_paise)}
          </Line>
        ))
      )}
      <p className="text-xs text-[color:var(--text-tertiary)]">
        Upstox's own figures for the year
        {pack.charges.state === 'ok' ? '.' : '; a segment you traded in is not read yet.'}
      </p>
    </Card>
  )
}

export function LeftOutCard({ pack }: { pack: TaxPack }) {
  return (
    <Card title="Not computed here">
      <Sentences lines={pack.not_computed} />
      {pack.not_computed_sales.length > 0 && (
        <div className={RULE}>
          <h3 className="text-sm font-semibold">Sales outside this paper</h3>
          {pack.not_computed_sales.map((sale) => (
            <Line
              key={`${sale.instrument}-${sale.day}`}
              label={sale.instrument}
              sub={`Sold ${dayText(sale.day)}`}
            >
              {rs(sale.amount_paise)}
            </Line>
          ))}
        </div>
      )}
      {pack.notes.length > 0 && (
        <div className={RULE}>
          <h3 className="pb-1 text-sm font-semibold">How it was worked out</h3>
          <Sentences lines={pack.notes} />
        </div>
      )}
    </Card>
  )
}

/** The trade history a year rests on, and whether a part of it is still being read. */
export function readingOf(sources: readonly TaxSource[]) {
  const parts = sources.filter((s) => s.kind === 'trades' && s.segment !== null)
  const named = (state: TaxSource['state'][]) =>
    parts.filter((s) => state.includes(s.state)).map((s) => s.segment as ReportSegment)
  return {
    total: parts.length,
    done: named(['ok']).length,
    reading: named(['reading']),
    unread: named(['unavailable', 'partial']),
  }
}

/**
 * Shown only while the year's trade history is not all read: how many parts are done, which are
 * being read, and one tap to read the rest through the reader the Upstox tab already uses.
 */
export function ReadsCard({ pack, onRead }: { pack: TaxPack; onRead: () => void }) {
  const state = readingOf(pack.inputs.sources)
  const send = useSend()
  if (state.total === 0 || state.done === state.total) return null
  const read = () =>
    send
      .run(async () => {
        for (const segment of state.unread) {
          await portfolioCall('report_trades', { fy: pack.fy, segment })
        }
      })
      .then(onRead)
  const names = (list: ReportSegment[]) => list.map((s) => SEGMENT_WORDS[s]).join(', ')
  return (
    <Card title="Reading from Upstox">
      <p className="text-sm text-[color:var(--text-secondary)]" aria-live="polite">
        {state.reading.length > 0 || send.pending
          ? `Reading this year from Upstox. ${state.done} of ${state.total} parts done.`
          : `${state.done} of ${state.total} parts of this year's trade history are read.`}
        {state.reading.length > 0 && ` Being read: ${names(state.reading)}.`}
        {state.unread.length > 0 && ` Not read in full: ${names(state.unread)}.`}
      </p>
      {send.problem && (
        <p className="text-sm font-semibold text-[color:var(--warning-text)]">
          {send.problem.message}
        </p>
      )}
      {state.unread.length > 0 && (
        <button type="button" onClick={read} disabled={send.pending} className={OUTLINE}>
          <RefreshCw aria-hidden="true" className="size-4" />
          {send.pending ? 'Reading' : 'Read this year'}
        </button>
      )}
    </Card>
  )
}

const SCHEDULES: [TaxSchedule, string][] = [
  ['capital_gains', 'Capital gains'],
  ['business', 'Business income'],
  ['losses', 'Losses'],
  ['dividends', 'Dividends'],
]

export function Downloads({ pack }: { pack: TaxPack }) {
  const send = useSend()
  const get = (schedule: TaxSchedule) =>
    send.run(async () => {
      const result = await getTaxCsv({ fy: pack.fy, member: pack.taxpayer, schedule })
      if (result.enabled) saveDownload(result.data)
    })
  return (
    <Card title="Download CSV">
      <div className="grid grid-cols-2 gap-2">
        {SCHEDULES.map(([schedule, label]) => (
          <button
            key={schedule}
            type="button"
            disabled={send.pending}
            onClick={() => void get(schedule)}
            className={OUTLINE}
          >
            <Download aria-hidden="true" className="size-4 shrink-0" />
            {label}
          </button>
        ))}
      </div>
      {send.problem && (
        <p className="text-sm font-semibold text-[color:var(--warning-text)]" aria-live="polite">
          {send.problem.message}
        </p>
      )}
    </Card>
  )
}
