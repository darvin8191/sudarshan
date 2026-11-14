import { BottomSheet } from '@/sudarshan/watchlist/BottomSheet'
import {
  type DayFacts,
  dayWords,
  type ExchangeHours,
  hhmm,
  nextOpenAt,
  whenWords,
} from './marketPhase'

/** The exchanges the sheet lists, in the order a trader reads them. */
const EXCHANGES = [
  ['NSE', 'NSE cash'],
  ['NFO', 'NSE F&O'],
  ['BSE', 'BSE cash'],
  ['BFO', 'BSE F&O'],
  ['MCX', 'MCX'],
] as const

/** "09:15-15:30 · pre-open from 09:00 · closing auction to about 15:40". */
export function hoursWords(hours: ExchangeHours | undefined): string {
  if (!hours) return 'Closed today'
  const parts = [`${hhmm(hours.open)}-${hhmm(hours.close)}`]
  if (hours.preOpen !== null) parts.push(`pre-open from ${hhmm(hours.preOpen)}`)
  if (hours.auctionUntil !== null)
    parts.push(`closing auction to about ${hhmm(hours.auctionUntil)}`)
  return parts.join(' · ')
}

/** Each expiry the facts list, today's first: "Today: NIFTY · contracts stop at 15:30". */
export function expiryLines(facts: DayFacts): string[] {
  const when = (day: string) => (day === facts.day ? 'Today' : dayWords(day))
  const stop = (at: number | null) => (at === null ? '' : ` · contracts stop at ${hhmm(at)}`)
  const lines = (facts.expiries ?? []).map(
    (each) => `${when(each.day)}: ${each.underlying}${stop(each.stopsAt)}`
  )
  for (const each of facts.stockExpiries ?? []) {
    const stocks = each.count === 1 ? '1 stock' : `${each.count} stocks`
    lines.push(`${when(each.day)}: F&O of ${stocks}${stop(each.stopsAt)}`)
  }
  return lines
}

/**
 * The day's hours for every exchange, the expiries of today and of the next trading day, and the
 * next open, from the server's facts (M3.5, M3.6). Read-only: nothing here can be pressed but Close.
 */
export function HoursSheet({
  facts,
  now,
  open,
  onOpenChange,
}: {
  facts: DayFacts
  now: number
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const note = [facts.note, facts.occasion].filter(Boolean).join(' · ')
  const expiries = expiryLines(facts)
  const next = nextOpenAt(facts, now)
  return (
    <BottomSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Market hours"
      description={note ? `${dayWords(facts.day)} · ${note}` : dayWords(facts.day)}
    >
      <dl className="divide-y divide-[color:var(--divider)]">
        {EXCHANGES.map(([code, name]) => (
          <div key={code} className="flex min-h-12 items-center justify-between gap-3 py-2">
            <dt className="text-sm font-semibold">{name}</dt>
            <dd
              className={
                facts.exchanges[code]
                  ? 'text-right text-sm font-medium'
                  : 'text-right text-sm font-medium text-[color:var(--text-tertiary)]'
              }
            >
              {hoursWords(facts.exchanges[code])}
            </dd>
          </div>
        ))}
      </dl>
      <h3 className="mt-3 text-[0.8125rem] font-bold text-[color:var(--text-secondary)]">
        Expiries
      </h3>
      {facts.expiries === null ? (
        <p className="mt-1 text-sm text-[color:var(--text-tertiary)]">
          Read from the master contract once it is ready.
        </p>
      ) : expiries.length === 0 ? (
        <p className="mt-1 text-sm text-[color:var(--text-tertiary)]">
          None today or on the next trading day.
        </p>
      ) : (
        <ul className="mt-1 flex flex-col gap-1 text-sm font-medium">
          {expiries.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-sm font-medium">
        {next === null
          ? 'The next open is not in the calendar yet.'
          : `Next open: ${whenWords(next, now)}`}
      </p>
      <p className="mt-1 text-xs text-[color:var(--text-tertiary)]">India time (IST)</p>
    </BottomSheet>
  )
}
