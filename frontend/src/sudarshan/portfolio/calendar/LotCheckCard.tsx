// SUDARSHAN P7 (M7.27, M7.36; design p7 4.3, 6.3): the "Check" card. After any bonus or split the
// owner applied, and every night, the service compares what the tax lots hold of each share with
// what the Upstox account held. A share that differs is named here with both figures and what to
// do; the working paper marks its sales "to check". Where the service can prove the change that
// makes the two agree (an applied bonus or split read the other way round, or taken out when it
// was counted twice), the row offers it as one tap, sent as `ca_decide` from the decision's
// version. Quantities at a day's close, not market data: the card says which day. Shown first on
// the Calendar and on the Tax view, never to a family viewer.
import { ArrowLeftRight, TriangleAlert, Undo2 } from 'lucide-react'
import { decideCorporateAction } from '../api'
import { ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { dayText } from '../tab/format'
import type { LotCheck, LotCheckRow } from '../types'
import { UPSTOX_SCOPE } from './confirm'

const CARD =
  'flex flex-col gap-2 rounded-[1.125rem] border border-[color:var(--gold)] bg-[color:var(--gold-tint)] px-4 py-3.5'

/** What the row's one tap does, in the owner's words. */
export function fixLabel(row: LotCheckRow): string | null {
  if (row.fix === null) return null
  return row.fix.state === 'applied'
    ? 'Apply it the other way round'
    : 'Take this one out of my lots'
}

function Row({ row, onDone }: { row: LotCheckRow; onDone: () => void }) {
  const send = useSend()
  const label = fixLabel(row)
  const fix = row.fix
  const apply = async () => {
    if (fix === null) return
    const done = await send.run(() =>
      decideCorporateAction({
        id: fix.event_id,
        scope: UPSTOX_SCOPE,
        state: fix.state,
        // A decision taken out keeps no ratio; the service reads none for it.
        ratio_num: fix.ratio_num ?? 1,
        ratio_den: fix.ratio_den ?? 1,
        version: fix.version,
      })
    )
    if (done) onDone()
  }
  return (
    <li className="flex flex-col gap-2 text-sm">
      <span>{row.words}</span>
      <ProblemLine problem={send.problem} />
      {label !== null && (
        <button
          type="button"
          className={SECONDARY}
          disabled={send.pending}
          onClick={() => void apply()}
        >
          {fix?.state === 'applied' ? (
            <ArrowLeftRight aria-hidden="true" className="size-4" />
          ) : (
            <Undo2 aria-hidden="true" className="size-4" />
          )}
          {label}
        </button>
      )}
    </li>
  )
}

export function LotCheckCard({
  check,
  onDone,
}: {
  check: LotCheck | undefined
  /** After a fix is sent: read the check (and whatever rests on the lots) again. */
  onDone: () => void
}) {
  if (!check || check.state !== 'ok' || check.rows.length === 0) return null
  return (
    <section aria-label="Check" className={CARD}>
      <h3 className="flex items-center gap-1.5 text-base font-bold text-[color:var(--warning-text)]">
        <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
        Check
      </h3>
      <p className="text-sm font-semibold">
        The tax lots and your Upstox holdings differ for {check.rows.length}{' '}
        {check.rows.length === 1 ? 'share' : 'shares'}
        {check.day !== null && `, compared at the close of ${dayText(check.day)}`}.
      </p>
      <ul className="flex flex-col gap-2.5">
        {check.rows.map((row) => (
          <Row key={row.isin} row={row} onDone={onDone} />
        ))}
      </ul>
      <p className="text-xs text-[color:var(--text-secondary)]">
        The tax working paper marks sales of{' '}
        {check.rows.length === 1 ? 'this share' : 'these shares'} “to check” until the two agree.
      </p>
    </section>
  )
}
