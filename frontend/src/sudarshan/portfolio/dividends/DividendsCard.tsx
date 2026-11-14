// SUDARSHAN P7 F5 (M7.13, M7.29): the Dividends card of the Tax view and its confirm sheet
// (design p7 9.1). Expected is what Upstox's corporate actions say on what was held the day before
// the ex-date; "credited" is only what the owner confirms from his bank. A company likely to
// deduct tax is flagged with an icon and the words. Dividends are facts, not market data: the card
// says when it was worked out. Behind the insights switch, with its own sentence while that is off.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BadgeCheck, ChevronRight, CircleAlert, CircleSlash, Clock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import { getDividends, PortfolioError, problemOf, setDividend } from '../api'
import { dateTimeOfInstant } from '../days'
import { Box } from '../sheets/EntryFields'
import { dayToInput, inputToDay, parseRupees, rupeesInput } from '../sheets/parse'
import { PRIMARY, ProblemLine, SECONDARY } from '../sheets/TabFields'
import { useSend } from '../sheets/useSend'
import { PORTFOLIO_KEY } from '../tab/ActivityTab'
import { dayText, quantityText, rupeesText as rs } from '../tab/format'
import type { DividendLine, DividendPayer, DividendState } from '../types'

export const DIVIDENDS_KEY = [...PORTFOLIO_KEY, 'dividends'] as const
export const DIVIDENDS_OFF =
  'Dividends from Upstox’s corporate actions are switched off on the server. They show here once a release switches them on.'
const CARD = 'rounded-[1.125rem] border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]'
const STATES: Record<DividendState, { word: string; icon: typeof Clock }> = {
  expected: { word: 'Expected', icon: Clock },
  confirmed: { word: 'Credited', icon: BadgeCheck },
  not_received: { word: 'Not received', icon: CircleSlash },
}

/** What a line counts for: what was paid once confirmed, nothing when not received, else expected. */
export function lineAmount(line: DividendLine): number {
  if (line.state === 'not_received') return 0
  if (line.state === 'confirmed') return (line.credited_paise ?? 0) + (line.tds_paise ?? 0)
  return line.expected_paise
}

function ConfirmSheet({
  line,
  onClose,
  onSaved,
}: {
  line: DividendLine | null
  onClose: () => void
  onSaved: () => void
}) {
  const [credited, setCredited] = useState('')
  const [tds, setTds] = useState('')
  const [day, setDay] = useState('')
  const send = useSend()
  const { clear } = send
  useEffect(() => {
    if (line === null) return
    setCredited(rupeesInput(line.credited_paise ?? line.expected_paise))
    setTds(line.tds_paise === null ? '' : rupeesInput(line.tds_paise))
    setDay(line.credited_day === null ? '' : dayToInput(line.credited_day))
    clear()
  }, [line, clear])
  const set = async (state: DividendState) => {
    if (line === null) return
    const done = await send.run(() =>
      setDividend({
        id: line.id,
        version: line.version,
        state,
        ...(state === 'confirmed'
          ? {
              credited_paise: parseRupees(credited),
              tds_paise: tds.trim() === '' ? null : parseRupees(tds),
              credited_day: day === '' ? null : inputToDay(day),
            }
          : {}),
      })
    )
    if (done) onSaved()
  }
  return (
    <BottomSheet
      open={line !== null}
      onOpenChange={(next) => !next && onClose()}
      title={line?.name ?? line?.isin ?? 'Dividend'}
      description={
        line ? `Ex-date ${dayText(line.ex_day)} · ${quantityText(line.qty_u)} held` : undefined
      }
    >
      {line && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-[color:var(--text-secondary)]">
            Expected {rs(line.expected_paise)}. Enter what reached your bank and any tax deducted;
            the dividend counted is the two together.
          </p>
          <Box label="Credited to your bank, in rupees" value={credited} onChange={setCredited} />
          <Box
            label="Tax deducted (TDS), in rupees"
            value={tds}
            onChange={setTds}
            hint="Leave empty if none was deducted."
          />
          <Box label="Day it was credited" type="date" value={day} onChange={setDay} />
          <ProblemLine problem={send.problem} />
          <button
            type="button"
            className={PRIMARY}
            disabled={send.pending}
            onClick={() => void set('confirmed')}
          >
            Confirm as credited
          </button>
          <button
            type="button"
            className={SECONDARY}
            disabled={send.pending}
            onClick={() => void set('not_received')}
          >
            Not received
          </button>
          {line.state !== 'expected' && (
            <button
              type="button"
              className={SECONDARY}
              disabled={send.pending}
              onClick={() => void set('expected')}
            >
              Back to expected
            </button>
          )}
        </div>
      )}
    </BottomSheet>
  )
}

function Payer({ payer, onLine }: { payer: DividendPayer; onLine: (line: DividendLine) => void }) {
  return (
    <li className="flex flex-col gap-1 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 text-[0.9375rem] font-bold break-words">
          {payer.name ?? payer.isin}
        </span>
        <span className="shrink-0 text-[0.9375rem] font-bold whitespace-nowrap">
          {rs(payer.expected_paise)}
        </span>
      </div>
      <span className="text-xs text-[color:var(--text-tertiary)]">
        Expected in all · confirmed {rs(payer.confirmed_paise)}
      </span>
      {payer.tds_likely && (
        <span className="flex gap-1.5 text-sm font-semibold text-[color:var(--warning-text)]">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <span>
            TDS likely
            {payer.tds_expected_paise !== null && `: about ${rs(payer.tds_expected_paise)}`}. Check
            Form 26AS.
          </span>
        </span>
      )}
      {payer.lines.map((line) => {
        const state = STATES[line.state]
        return (
          <button
            key={line.id}
            type="button"
            onClick={() => onLine(line)}
            aria-label={`${payer.name ?? payer.isin}, ex-date ${dayText(line.ex_day)}: ${state.word}`}
            className="flex min-h-12 w-full items-center gap-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">
                {dayText(line.ex_day)} · {quantityText(line.qty_u)} held
              </span>
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--text-secondary)]">
                <state.icon aria-hidden="true" className="size-3.5" />
                {state.word}
              </span>
            </span>
            <span className="shrink-0 text-[0.9375rem] whitespace-nowrap">
              {rs(lineAmount(line))}
            </span>
            <ChevronRight aria-hidden="true" className="size-4 shrink-0" />
          </button>
        )
      })}
    </li>
  )
}

export function DividendsCard({
  fy,
  member,
  on,
}: {
  fy: number
  member: number | undefined
  /** The insights switch, as the portfolio's state says it. */
  on: boolean
}) {
  const queries = useQueryClient()
  const [line, setLine] = useState<DividendLine | null>(null)
  const query = useQuery({
    queryKey: [...DIVIDENDS_KEY, fy, member ?? null] as const,
    queryFn: async ({ signal }) => {
      const result = await getDividends({ fy, member }, signal)
      if (!result.enabled) {
        throw new PortfolioError({ code: 'unavailable', message: result.message, entryId: null })
      }
      return result.data
    },
    enabled: on,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const problem = query.error ? problemOf(query.error) : null
  const off = !on || problem?.code === 'not_ready'
  const year = query.data?.dividends
  return (
    <section aria-label="Dividends" className={`${CARD} flex flex-col gap-2`}>
      <h2 className="text-xs font-semibold text-muted-foreground">Dividends</h2>
      {off ? (
        <p className="text-sm text-[color:var(--text-secondary)]">{DIVIDENDS_OFF}</p>
      ) : problem ? (
        <p className="text-sm text-[color:var(--text-secondary)]">{problem.message}</p>
      ) : year === undefined || query.data === undefined ? (
        <p className="text-sm text-[color:var(--text-secondary)]">Reading the year’s dividends.</p>
      ) : (
        <>
          <div className="flex flex-col">
            <span className="sd-display text-[1.75rem] leading-tight font-bold break-words">
              {rs(year.total_paise)}
            </span>
            <span className="text-xs text-[color:var(--text-tertiary)]">
              Dividends of the year: confirmed as paid, the rest as expected
              {year.unconfirmed_count > 0 && ` · ${year.unconfirmed_count} not confirmed yet`}
            </span>
          </div>
          {year.tds_expected_paise > 0 && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              TDS likely in all: about {rs(year.tds_expected_paise)}.
            </p>
          )}
          {year.payers.length === 0 ? (
            <p className="text-sm text-[color:var(--text-secondary)]">
              No dividend is listed for what you held in this year.
            </p>
          ) : (
            <ul className="flex flex-col divide-y divide-[color:var(--divider)]">
              {year.payers.map((payer) => (
                <Payer key={payer.isin} payer={payer} onLine={setLine} />
              ))}
            </ul>
          )}
          <p className="text-xs text-[color:var(--text-tertiary)]">
            As of {dateTimeOfInstant(query.data.as_of).slice(0, -3)} · from Upstox’s corporate
            actions and your own confirmations
          </p>
        </>
      )}
      <ConfirmSheet
        line={line}
        onClose={() => setLine(null)}
        onSaved={() => {
          setLine(null)
          // The year's dividends, and the working paper that counts them.
          void queries.invalidateQueries({ queryKey: DIVIDENDS_KEY })
          void queries.invalidateQueries({ queryKey: [...PORTFOLIO_KEY, 'tax'] })
        }}
      />
    </section>
  )
}
